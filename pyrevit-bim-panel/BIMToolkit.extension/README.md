# BIMToolkit — AI 驱动的 Revit 自动化工具集

> pyRevit 扩展 | Revit 2019 | IronPython 2.7 | 14 个按钮（收敛中，目标 8 入口）

**引擎说明（2026-10-07 校正）**：此前这里写"IronPython 2.7 + CPython 3 双引擎"。
实测没有任何按钮声明引擎（零 `config.yml`、零 `#! python3`），pyRevit 因此一律用
默认引擎 **IronPython 2.7**；`CPY3123` 引擎装了但没有按钮在用。全部代码按
IronPython 2.7 语法编写并由真 `ipy.exe` 做编译门禁（`tools/ipy_selfcheck.py`）。

## 项目定位

面向建筑施工企业的 **AI + BIM 自动化** 插件，将 AI 大模型能力深度集成到 Revit 工作流中，
覆盖从「图纸识别 → 模型生成 → 规范审查 → 工程量 → 4D 进度关联」的全链路自动化。

**与商业产品（如 CONCETTO）的差异化优势**：
- 本地部署，数据不出企业内网
- 支持 Revit 2019 等旧版本（国内企业大量使用）
- 开放架构，可对接企业自有规范库和定额库
- AI 指令支持自然语言操控 Revit，降低使用门槛

---

## 功能架构

### Tab 1: BIM 工具（通用工具集）

| 按钮 | 功能 | 技术要点 |
|------|------|----------|
| **AI 指令** | 自然语言 → Revit Python 代码，自动执行 | DeepSeek API + 自愈重试（最多3轮） |
| **一键统计** | 构件分类计数、面积体积、参数汇总 | FilteredElementCollector + 参数读取 |
| **批量改参** | 按规则批量修改构件参数 | CSV 规则导入 + 事务管理 + WinForms TaskDialog 确认框 |
| **构件清单导出** | 导出构件清单到 CSV/Excel | WinForms SaveFileDialog + UTF-8 BOM CSV 写入 |
| **楼层清点** | 按楼层统计构件分布 | Level 参数关联 |
| **BIM 体检** | 模型健康度评估（警告/族/参数化） | 5维评分体系 0~100分 |
| **DWG翻模** | DWG 图纸导入并识别翻模 | DXF 解析 + 几何映射 + 多路径 Python 发现 |
| **进度关联** | 构件与施工进度计划关联 | 4D 模拟数据准备 + WinForms TaskDialog |
| **MCP Bridge** | 外部 AI 控制器与 Revit 通信 | Socket 桥接 + JSON 协议 + 三档命令策略 |
| **配置建模** | 选 `buildings/*.yaml` 配方，配置驱动自动建模 | 干跑校验 → 清场确认 → genbuild 引擎（CPython 子进程） |

> 2026-10-07 移除：`翻模体检`（它读的 `*_report.json` 全仓没有生产者，且会照
> ElementId 盲删当前文档构件）、`Create Villa`（与「一键建模」重合且尺寸硬编码，
> `NewFootPrintRoof` 用的是 CPython 元组解包写法，在 IronPython 2.7 下大概率整体回滚）。

### Tab 2: AI 智建（核心差异化功能）

| 按钮 | 功能 | 技术要点 |
|------|------|----------|
| **一键建模** | 读取 building_params.json，自动创建完整别墅模型 | model_builder.py 引擎：标高/墙/板/坡屋顶/门窗/阳台 |
| **规范审查** | 5类国标规范自动审查 + 评分 | 消防/抗震/结构/面积/节能；三态（未检查项不伪装成通过） |
| **工程量统计** | 工程量清单 + 造价估算 | 混凝土/模板/钢筋 |
| **智能出图** | 自动创建平面图视图（立面图受限） | ViewPlan.Create；立面图 API 在 Revit 2019+IronPython 不兼容 |

> 2026-10-07 移除：`一键演示`（另三个按钮的复制粘贴，且缺"模型已有构件"检查，
> 二次点击会把同一栋楼叠上去）、`施工方案`（7 阶段数组是硬编码的，docstring 承诺的
> TXT 导出并不存在）、`自动标注`（每类只出一条水平尺寸链，非共线构件整链失败）。
>
> 面板名 `AI 智建` 目前名不副实：留下的 4 个里只有「规范审查」真调用模型，
> 且规则引擎负责判定、AI 只出解释。改名与合并见 `docs/收敛方案-19按钮到8入口.md`。

---

## 技术栈

- **宿主**: pyRevit 6.5.5（含 Revit 2019 支持的最后一支；pyRevit 7.0 已砍 Revit <2021）
- **引擎**: IronPython 2.7（没有任何按钮声明引擎，故一律走默认引擎）
- **API**: Autodesk Revit API 2019
- **AI**: DeepSeek API (OpenAI 兼容协议)，密钥只走环境变量 `DEEPSEEK_API_KEY`
- **UI**: WinForms (TaskDialog/SaveFileDialog) — 避免 WPF 在 Revit 2019 legacy loader 崩溃

## 目录结构

```
BIMToolkit.extension/
├── extension.json
├── lib/                          # 共用库
│   ├── bimlib.py                 # 基础工具（unicode安全/参数读写/CSV）
│   ├── bimhealth.py              # 健康度评分引擎
│   ├── llm_client.py             # DeepSeek API 客户端
│   ├── model_builder.py          # 建筑模型自动生成引擎
│   ├── building_params.json      # 别墅模板参数（2层/坡屋顶/阳台/门窗）
│   ├── standards_checker.py      # 规范审查引擎
│   ├── standards_quick.json      # 国标阈值数据
│   └── cost_estimator.py         # 工程量/造价估算
├── BIMToolkit.tab/
│   ├── BIM 工具.panel/           # 12个通用工具按钮
│   │   ├── AI 指令.pushbutton/
│   │   ├── 一键统计.pushbutton/
│   │   └── ...
│   └── AI 智建.panel/            # 4 个按钮（原 7 个，见上方移除说明）
│       ├── 一键建模.pushbutton/
│       ├── 规范审查.pushbutton/
│       ├── 工程量统计.pushbutton/
│       └── 智能出图.pushbutton/
```

## 核心设计

### AI 指令（自然语言 → Revit 操作）

```
用户输入 "把所有标高1的墙厚度改成200mm"
    ↓
抓取模型上下文（构件数/类别/标高/选择集）
    ↓
DeepSeek 生成 Revit Python 代码
    ↓
Notepad 预览 + MessageBox 确认（安全门）
    ↓
Transaction 中执行 → 失败自动回滚
    ↓
出错 → 回喂 AI 修正（最多3轮）→ 重新确认执行
```

### 一键建模（参数化 → 完整模型）

```
building_params.json（建筑参数）
    ↓
解析楼层数/层高/墙厚/门窗尺寸/屋顶形式
    ↓
逐层创建: 标高 → 墙 → 板 → (顶层)屋顶 → 门窗
    ↓
每步独立 Transaction + 进度输出
```

### 配置建模（YAML 配方 → 整栋楼）

```
buildings/*.yaml（层/墙段/开洞/板/楼梯/屋顶/栏杆/材质）
    ↓
【第 1 段】干跑：静态校验（开洞必须落墙、梯段反算、层高一致性）+ ping 桥
    ↓  不通过就停在这里，模型一个字节都不动
【第 2 段】清场确认：列出本次实际会被删除的类别（从引擎现读，不硬编码）
    ↓  用户点「是」才继续
【第 3 段】正式构建：清场 + 标高 → 轴网 → 墙 → 板 → 楼梯 → 屋顶 → 栏杆 → 门窗 → 材质
```

> **为什么走外部 CPython 子进程**：genbuild 引擎依赖 PyYAML，而 Revit 2019 的 pyRevit
> 跑的是 IronPython 2.7（没有 yaml）。按钮只做「选配方 / 干跑 / 确认 / 调子进程 / 回显日志」，
> 与 `DWG翻模` 把 ezdxf 的活丢给外部 CPython 是同一套路。
> 路径由按钮目录下的 `genbuild_config.json` 指定。

### 全链路演示流水线（原「一键演示」按钮，2026-10-07 移除）

> 移除原因不是流程没价值，而是它把下面几个按钮的逻辑**复制**了一份（出图那段
> 是手抄的），且缺"模型已有构件"检查 —— 第二次点击会把同一栋楼叠上去，而叠墙
> 正是 `DWG翻模` 记录过的崩溃前因。演示时按下面顺序手动点即可。

```
step1: 一键建模                    → model_builder.build_model() 从 JSON 生成别墅模型
    ↓
step2: BIM 体检                    → bimhealth 扫描（警告/族/参数化 + 健康分）
    ↓
step3: 规范审查                    → standards_checker.run_review（国标判定 + AI 解释）
    ↓
step4: 智能出图                    → ViewPlan.Create 建楼层平面图
    ↓
step5: 工程量统计 / 进度关联       → 工程量与造价参考值、4D 步骤数据
```

### 规范审查（实际检查项）

```
提取模型参数（标高数/楼层数/高度/层高列表/墙厚/面积估算）
    ↓
5类检查 —— 只列代码真正判的东西，不写没实现的条目：
  - 消防 (GB50016): 建筑高度限值、逐层层高限值、是否设了耐火极限参数
  - 抗震 (GB50011): 抗震限高、平均层高偏大时提示验算层间刚度
  - 结构 (GB50010): 墙厚最小值、楼板跨厚比提示（info）
  - 面积 (GBT50353): 外包尺寸估算的总建筑面积（info，非按规范计算规则）
  - 节能 (GB50176): 需 standards 里有 min_wall_insulation_mm 才会判；
                     当前该值为 0 ⇒ 明确报「未检查」，不再静默缺席
    ↓
加权评分 0~100 → 等级（优秀/良好/及格/需整改）
```

## 关键踩坑记录

1. **IronPython 2.7 无 f-string** — 全部使用 `%` 或 `.format()`
2. **unicode 拼接炸弹** — 中文 + str 隐式 ascii 解码失败，统一用 `to_text()` 转 unicode
3. **Wall.Create 重载歧义** — 4参重载解析不可靠，必须用8参重载（唯一无歧义）
4. **幕墙/叠层墙无 CompoundStructure** — 必须按 `WallKind.Basic` 过滤基本墙类型
5. **WinForms 布局顺序** — `DockStyle.Fill` 控件必须最先 Add，否则覆盖其他控件
6. **IronPython FilteredElementCollector** — `.Count` 属性不可用，必须 `ToElements()` 后 `len()`
7. **ViewSection API 在 Revit 2019 + IronPython 2.7 完全不可用** — 尝试了 5 种方案（直接调用、clr.GetClrType、InvokeMember 反射、ElevationMarker、CreateSection+Transform），均因 IronPython 对 CLR 静态方法的类型解析 bug 或 .NET 层 StandardError 而失败。最终降级为仅创建平面图，立面图提示用户手动创建。**升级至 CPython3 引擎或 Revit 2022+ 可解决**。
8. **WPF forms.alert() 在 Revit 2019 legacy loader 崩溃** — 所有 UI 对话框改用 WinForms TaskDialog；文件保存改用 System.Windows.Forms.SaveFileDialog
9. **硬编码 ElementId 跨文件失效** — 改为按 elevation 排序动态发现标高（该写法最早出自 Create Villa，该按钮已于 2026-10-07 移除，教训保留）
10. **硬编码 Python 路径导致 DWG翻模失败** — 增加多路径 fallback：`.workbuddy` → `D:\Python` → `C:\Python39` → 系统 PATH

## 已知限制

| 功能 | 限制 | 原因 | 解决方案 |
|------|------|------|----------|
| 智能出图 — 立面图 | 无法自动创建立面视图 | ViewSection.CreateSection 在 Revit 2019 + IronPython 2.7 下抛出 StandardError | 手动在项目浏览器创建立面；或切换 pyRevit 至 CPython3 引擎 |
| 尺寸标注 | **暂无入口**（原「自动标注」按钮已移除） | 每类只出一条水平尺寸链、强制 X 排序，非共线构件整链失败 | 需要时按轴线分组重做，约 3 人日 |

## 代码质量改进（v1.1）

### 架构规范化
- **统一 main() 模式**：所有脚本逻辑封装在 `main()` 函数，避免模块级执行副作用
- **动态资源发现**：按 elevation 排序动态查找标高、DWG翻模多路径 Python 发现
- **UI 兼容性加固**：全面替换 WPF → WinForms，确保 Revit 2019 legacy loader 稳定运行

### 安全性提升
- **批量改参确认框**：预览后弹出 TaskDialog 确认，防止误写入（可 Ctrl+Z 撤销）
- **构件清单导出对话框**：使用 SaveFileDialog 让用户选择保存路径，而非硬编码

### 错误处理增强
- **CSV 文件缺失提示**：批量改参、进度关联均检查 CSV 存在性并给出友好提示
- **空模型检测**：进度关联在无构件时提前退出并提示用户
- **参数写入失败反馈**：批量改参逐条报告失败原因（只读参数/类型不匹配等）

## 安装使用

1. 安装 [pyRevit](https://github.com/eirannejad/pyRevit)
2. 将 `BIMToolkit.extension` 文件夹复制到 pyRevit 扩展目录：
   ```
   %APPDATA%\pyRevit\Extensions\
   ```
3. 配置 DeepSeek API Key（二选一）：
   - 环境变量: `DEEPSEEK_API_KEY=sk-xxx`
   - 配置文件: `lib/deepseek_config.json` 中填写 `api_key`
4. Revit 中点击 pyRevit 按钮刷新，即可看到 BIMToolkit tab

### CSV 模板文件说明

以下功能需要配套 CSV 文件（与对应 `.pushbutton` 脚本同目录）：

| 功能 | CSV 文件名 | 列格式 |
|------|-----------|--------|
| **批量改参** | `batch_params.csv` | `Category,Type,ParamName,Value` |
| **进度关联** | `schedule_summary.csv` | `uid,名称,楼层,类别,类型,计划开始,计划完成,状态` |

仓库已附带模板文件，按列填好数据后运行对应按钮即可。

## 适用场景

- 建筑施工企业 BIM 中心：批量建模、规范审查、工程量统计
- 设计院：AI 辅助建模、自动出图标注
- 施工方：进度关联（4D 数据准备）
- 求职作品集：展示 AI + BIM 全链路自动化能力

---

*本项目为个人独立开发，从架构设计到代码实现全部原创。*
