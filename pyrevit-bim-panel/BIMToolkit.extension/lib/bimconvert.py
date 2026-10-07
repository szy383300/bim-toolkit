# -*- coding: utf-8 -*-
u"""
bimconvert.py —— DWG 翻模的**统一入口**（薄封装，本身不含实现）。

实现已按职责拆成 5 个扁平模块（见下表），本文件只做汇总重导出，
因此 `from bimconvert import ...` 的用法完全不变，按钮脚本 / 测试台 / demo 均无需改动。

    bimconv_geom.py      单位换算 + 坐标校验 + 退化守卫（防 Revit 原生崩溃的第一道闸）
    bimconv_rules.py     读 mapping_rules.csv → 匹配 → 生成建设计划（纯 Python，可 headless 测）
    bimconv_walls.py     零散墙段链成连续墙线、墙段接合、门/窗寄宿索引
    bimconv_elements.py  各构件类型的创建（轴网/墙/柱/板/门/窗/房间/楼梯）
    bimconv_build.py     分批事务 + 失败抑制 + create_elements 编排

数据流：
  1) dwg_to_json.py（bim-dev 环境，有 ezdxf）把 DWG/DXF 读成中性 model_plan.json；
  2) plan_from_json() 按 mapping_rules.csv 把 JSON 实体映射成「计划建哪些构件」；
  3) DWG翻模.pushbutton 读 JSON + 规则 → 预览 → 确认 → create_elements() 真正写入。

为什么「读图」与「建构件」分开：pyRevit 的 IronPython 2.7 没有 ezdxf，无法在 Revit 内
直接读 DWG，所以读图放 bim-dev、建构件放 Revit，中间用 JSON 传递。

可靠性标签：mature = 接口稳、可直建；experimental = 接口试建、逐个 try/except 上报，
失败不中断整体（与 BIM 体检「错误可见化」同思路）。
"""

# ---- 几何基础层 ----
from bimconv_geom import (MM_TO_FT, MIN_WALL_SEGMENT_LEN, MIN_WALL_TOTAL_LEN,
                          _num_ok, _xyz, _point_of)

# ---- 规则与计划层 ----
from bimconv_rules import (RELIABILITY, load_rules, plan_from_json, match_rule)

# ---- 墙线处理层 ----
from bimconv_walls import (consolidate_walls, _join_walls,
                           _build_wall_index, _nearest_wall,
                           slab_plans_from_wall_loops)

# ---- 构件创建层 ----
from bimconv_elements import (_level_id, _wall_type, _column_symbol, _floor_type,
                              _create_grid, _create_wall, _create_column,
                              _create_slab, _create_point_symbol, _create_stair,
                              _create_door_window, _create_room)

# ---- 构建编排层 ----
from bimconv_build import (create_elements, _Batch, _WarnSuppressor,
                           _HAVE_FAILURES)

# DB 便捷重导出（旧版 bimconvert 在模块级就暴露 DB，保持兼容）
try:
    from pyrevit import DB
except Exception:
    DB = None
# MCP bridge / embedded context: pyrevit import may fail silently -> DB=None.
# Fall back to the direct RevitAPI reference (same fix as patch_lib_db.py).
if DB is None:
    try:
        import clr
        clr.AddReference("RevitAPI")
        from Autodesk.Revit import DB
    except Exception:
        DB = None


# 对外稳定 API（按钮脚本 / selftest / demo 实际用到的就这些）
__all__ = [
    # 常量与标签
    "MM_TO_FT", "MIN_WALL_SEGMENT_LEN", "MIN_WALL_TOTAL_LEN", "RELIABILITY",
    # 规则与计划
    "load_rules", "plan_from_json", "match_rule",
    # 墙线
    "consolidate_walls", "slab_plans_from_wall_loops",
    # 构建
    "create_elements",
]
