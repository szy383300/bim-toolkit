# bim-toolkit

用 Python 把设计院和工地的重复劳动自动化——CAD 批量出图、Revit 一键统计与改参、横道图进度与 BIM 模型联动（4D）。

> 这是《建筑信息化融合学习规划》的配套代码仓库，每个阶段一个目录，随学习推进逐步填充。

---

## 当前环境（2026-08-29 实测）

| 工具 | 版本 | 状态 |
|---|---|---|
| Python venv | `E:\AI-pyenvs\bim-dev`（3.13.14） | 已建 |
| AutoCAD | 2010（`C:\Program Files\AutoCAD 2010`） | COM 已打通，版本 `18.0s` |
| Revit | 2019（`D:\Autodesk\Revit 2019`） | 已装，`RevitAPI.dll` 可用 |
| pyRevit | 6.5.5（`C:\Program Files\pyRevit-Master`） | 已装，加载项已注册 |
| GanttProject | 3.3.3316（`D:\CAD\GanttProject`） | 已装 |
| Dynamo | 1.3 | 已装，`.addin` 待注册 |

**环境自检 6 项全部 PASS**（见 `env_check.py`）。

---

## 目录结构（随阶段推进）

| 目录 | 对应阶段 | 内容 | 状态 |
|---|---|---|---|
| （根目录） | 阶段 0 | `env_check.py`、`requirements.txt` | ✅ 已提交 |
| `dwg-batch-tool/` | 阶段 1 | DWG 批量转 PDF / 提取标题栏 / 统一图层 | ✅ 已构建（`dwg_inventory.py`） |
| `cad-smart-annotate/` | 阶段 2 | AutoCAD 智能标注脚本 | ✅ 已构建（`cad_annotate.py`，双后端） |
| `dynamo-scripts/` | 阶段 2 | Dynamo 图形文件与 Python 节点 | ⬜ 待开始 |
| `pyrevit-bim-panel/` | 阶段 3 | pyRevit 插件：一键统计 / 批量改参 / 楼层清点（+构件清单导出） | ✅ 已构建（已装入 `%APPDATA%\pyRevit\Extensions\BIMToolkit.extension`） |
| `bim-health-check/` | 阶段 4 | 参数化族生成 + BIM 体检报告 | ⬜ 待开始 |
| `schedule-automation/` | 阶段 5 | 进度解析 → 横道图 → 4D 关联 → 周报 | ✅ 已构建（`gan_schedule.py`，headless） |

---

## 快速开始

```bash
# 1. 创建并激活虚拟环境（已建好可跳过）
python -m venv E:\AI-pyenvs\bim-dev
E:\AI-pyenvs\bim-dev\Scripts\activate

# 2. 安装依赖
pip install -r requirements.txt

# 3. 运行环境自检
python env_check.py
```

`env_check.py` 会检查 6 项：虚拟环境、pywin32、AutoCAD COM 真实连接、Revit API 程序集、pythonnet、pyRevit。
第 3 项会**短暂弹出 AutoCAD 再自动关闭**，属正常现象。

---

## 依赖说明

| 包 | 用途 |
|---|---|
| `pywin32` | 通过 COM 驱动 AutoCAD（批量出图、标注、改图层） |
| `pythonnet` | 加载 .NET 程序集，打通 Revit API 通道 |
| `openpyxl` | 读写 Excel（门窗表、构件明细表） |
| `pandas` | 数据清洗与工程量统计 |
| `matplotlib` | 画横道图与统计图表 |
| `numpy` / `pillow` | 上述库的底层依赖 |

---

## 学习目标

用免费工具链（AutoCAD + Revit + pyRevit + GanttProject + Python）复刻商业软件（斑马进度 / P6 / Navisworks）的核心价值，形成的完整流水线：

```
进度计划(Excel/GanttProject) → 自动横道图 → 关联 Revit 构件 → 4D 推进模拟 → 一键周报
```

详见《建筑信息化融合学习规划》。
