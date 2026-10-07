# -*- coding: utf-8 -*-
"""AI 智建 > 施工方案 — 根据模型参数自动生成施工进度计划。

读取模型参数 → 生成 7 阶段施工方案 → HTML 展示 + 导出 TXT
"""
import os
import sys
import json
from datetime import date, timedelta

from Autodesk.Revit.DB import FilteredElementCollector, Level, Wall

uidoc = __revit__.ActiveUIDocument
doc = uidoc.Document

HERE = os.path.dirname(__file__)
EXT = os.path.dirname(os.path.dirname(os.path.dirname(HERE)))
LIB = os.path.join(EXT, "lib")
if LIB not in sys.path:
    sys.path.insert(0, LIB)

from bimlib import to_text, to_element_list, html_table
import standards_checker


def generate_plan(params):
    u"""生成施工方案 dict。"""
    floors = params.get("total_floors", 1)
    area = params.get("total_area_sqm", 0)
    height = params.get("building_height_mm", 0) / 1000.0

    base_days = max(5, int(area * 0.3))

    phases = [
        {
            "name": u"施工准备",
            "duration": max(3, floors * 2),
            "tasks": [
                u"场地平整与临时设施搭建",
                u"施工测量放线",
                u"临时水电接入",
                u"材料进场与检验",
            ],
        },
        {
            "name": u"基础工程",
            "duration": max(5, floors * 3),
            "tasks": [
                u"基坑开挖",
                u"基础垫层施工",
                u"基础钢筋绑扎",
                u"基础混凝土浇筑",
                u"基础回填",
            ],
        },
        {
            "name": u"主体结构",
            "duration": max(10, floors * 7),
            "tasks": [
                u"柱/墙钢筋绑扎 (%d层)" % floors,
                u"模板支设",
                u"混凝土浇筑",
                u"模板拆除与养护",
            ] * min(floors, 3),
        },
        {
            "name": u"屋面工程",
            "duration": max(3, floors * 2),
            "tasks": [
                u"屋面防水层施工",
                u"屋面保温层施工",
                u"屋面保护层施工",
            ],
        },
        {
            "name": u"装饰装修",
            "duration": max(8, floors * 5),
            "tasks": [
                u"内墙抹灰",
                u"楼地面施工",
                u"外墙装饰",
                u"门窗安装",
                u"油漆涂料",
            ],
        },
        {
            "name": u"安装工程",
            "duration": max(5, floors * 3),
            "tasks": [
                u"给排水管道安装",
                u"电气线路敷设",
                u"暖通设备安装",
                u"消防系统安装",
            ],
        },
        {
            "name": u"竣工验收",
            "duration": 3,
            "tasks": [
                u"自检与整改",
                u"资料整理与归档",
                u"竣工验收",
            ],
        },
    ]

    total_days = sum(p["duration"] for p in phases)

    start = date.today() + timedelta(days=7)
    for p in phases:
        p["start"] = start.isoformat()
        start += timedelta(days=p["duration"])
        p["end"] = start.isoformat()

    wall_count = 0
    try:
        walls = to_element_list(
            FilteredElementCollector(doc).OfClass(Wall)
            .WhereElementIsNotElementType())
        wall_count = len(walls)
    except Exception:
        pass

    equipment = [
        u"塔吊 1台" if floors >= 3 else u"物料提升机 1台",
        u"混凝土泵车 1台",
        u"钢筋加工设备 1套",
        u"模板及支撑体系",
    ]

    quality_checks = [
        u"钢筋隐蔽验收",
        u"混凝土试块检测",
        u"防水工程闭水试验",
        u"主体结构实体检测",
    ]

    return {
        "phases": phases,
        "total_days": total_days,
        "equipment": equipment,
        "quality_checks": quality_checks,
        "wall_count": wall_count,
        "floors": floors,
        "area_sqm": area,
    }


def show_plan(plan):
    try:
        from pyrevit import script
        output = script.get_output()
    except Exception:
        output = None

    if not output:
        return

    output.print_md(u"## 施工进度计划")
    output.print_md(u"")
    output.print_md(u"总工期: **%d 天** | 层数: **%d** | 面积: **%.1f m2**" % (
        plan["total_days"], plan["floors"], plan["area_sqm"]))
    output.print_md(u"")

    headers = [u"阶段", u"工期(天)", u"开始", u"结束", u"主要任务"]
    rows = []
    for p in plan["phases"]:
        tasks_str = u"; ".join(p["tasks"][:4])
        if len(p["tasks"]) > 4:
            tasks_str += u"..."
        rows.append([p["name"], str(p["duration"]),
                      p["start"], p["end"], tasks_str])
    output.print_html(html_table(headers, rows))

    output.print_md(u"")
    output.print_md(u"### 主要设备")
    for e in plan["equipment"]:
        output.print_md(u"- " + e)

    output.print_md(u"")
    output.print_md(u"### 质量控制要点")
    for q in plan["quality_checks"]:
        output.print_md(u"- " + q)

    output.print_md(u"")
    output.print_md(u"---")
    output.print_md(u"_AI 施工方案 | BIMToolkit_")


def main():
    try:
        from pyrevit import script
        output = script.get_output()
        output.print_md(u"## 正在生成施工方案...")
    except Exception:
        output = None

    params = standards_checker.extract_model_params(doc)
    plan = generate_plan(params)
    show_plan(plan)

    try:
        from Autodesk.Revit.UI import TaskDialog, TaskDialogCommonButtons
        dlg = TaskDialog(u"施工方案")
        dlg.MainInstruction = u"总工期: %d 天 (%d 个阶段)" % (
            plan["total_days"], len(plan["phases"]))
        dlg.MainContent = u"详细方案已在 pyRevit 控制台输出。"
        dlg.CommonButtons = TaskDialogCommonButtons.Ok
        dlg.Show()
    except Exception:
        pass


if __name__ == "__main__":
    main()
else:
    main()
