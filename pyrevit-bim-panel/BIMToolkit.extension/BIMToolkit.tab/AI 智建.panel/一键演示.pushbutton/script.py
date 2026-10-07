# -*- coding: utf-8 -*-
"""AI 智建 > 一键演示 — 完整工作流演示

串联执行：
  1. 一键建模（从 JSON 生成完整别墅模型）
  2. BIM 体检（模型健康度评估）
  3. 规范审查（5类国标审查）
  4. 智能出图（创建楼层平面图）
  5. 生成汇总报告

一键完成全流程演示，适合展示 AI + BIM 自动化能力。
"""
import os
import sys
import json

uidoc = __revit__.ActiveUIDocument
doc = uidoc.Document

HERE = os.path.dirname(__file__)
EXT = os.path.dirname(os.path.dirname(os.path.dirname(HERE)))
LIB = os.path.join(EXT, "lib")
if LIB not in sys.path:
    sys.path.insert(0, LIB)

from bimlib import to_text, to_element_list, html_table, all_model_elements
from model_builder import build_model
import standards_checker
from bimhealth import scan_warnings, scan_families, scan_parameters, health_score, health_grade

try:
    from pyrevit import script
    output = script.get_output()
except Exception:
    output = None


def _print(msg):
    if output:
        output.print_md(msg)
    else:
        print(msg)


def find_params_json():
    candidates = [
        os.path.join(LIB, "building_params.json"),
        os.path.join(os.path.dirname(HERE), "building_params.json"),
    ]
    try:
        doc_path = doc.PathName
        if doc_path:
            doc_dir = os.path.dirname(doc_path)
            candidates.insert(0, os.path.join(doc_dir, "building_params.json"))
    except Exception:
        pass
    for p in candidates:
        if os.path.exists(p):
            return p
    return None


def load_params():
    path = find_params_json()
    if not path:
        return None, None
    with open(path, "rb") as f:
        raw = f.read()
    if raw[:3] == b"\xef\xbb\xbf":
        raw = raw[3:]
    data = json.loads(raw.decode("utf-8"))
    return data, path


def step1_model(params):
    _print(u"## 步骤 1/4：一键建模")
    _print(u"")
    result = build_model(doc, params, _print)
    _print(u"")
    return result


def step2_health_check():
    _print(u"## 步骤 2/4：BIM 体检")
    _print(u"")

    from Autodesk.Revit.DB import (
        FilteredElementCollector, Level, View, ViewSheet, BuiltInCategory
    )
    # 构件总数用全部非类型构件（此前误用墙列表，口径错误）
    els = to_element_list(all_model_elements(doc))
    levels = to_element_list(FilteredElementCollector(doc).OfClass(Level))
    views = to_element_list(FilteredElementCollector(doc).OfClass(View))
    sheets = to_element_list(FilteredElementCollector(doc).OfClass(ViewSheet))

    warn = scan_warnings(doc)
    fam = scan_families(doc)
    # scan_parameters 内部以 all_model_elements(doc) 形式调用，必须传收 1 参的可调用
    param = scan_parameters(doc, all_model_elements)
    score = health_score(len(els), warn["warnings"], warn["errors"],
                         len(fam["orphan_families"]), param["missing_mark"])
    grade = health_grade(score)

    _print(u"### 顶层指标")
    summary = [
        [u"模型构件总数", len(els)],
        [u"楼层数", len(levels)],
        [u"视图数", len(views)],
        [u"图纸数", len(sheets)],
        [u"警告数", warn["warnings"]],
        [u"错误数", warn["errors"]],
    ]
    _print(html_table([u"指标", u"数值"], summary))
    _print(u"")
    _print(u"### 综合健康分：**%d / 100** — %s" % (score, grade))
    _print(u"")

    return {
        "score": score,
        "grade": grade,
        "elements": len(els),
        "levels": len(levels),
        "warnings": warn["warnings"],
        "errors": warn["errors"],
    }


def step3_standards_review():
    _print(u"## 步骤 3/4：规范审查")
    _print(u"")

    report = standards_checker.run_review(doc)
    score = report["score"]
    grade = report["grade"]
    checks = report["checks"]

    color = u"#27ae60" if score >= 75 else (
        u"#f39c12" if score >= 60 else u"#e74c3c")
    _print(u"### 综合评分: <span style='color:%s'>**%d 分 (%s)**</span>" % (
        color, score, grade))
    _print(u"")

    headers = [u"检查项", u"状态", u"说明", u"规范依据"]
    rows = []
    for c in checks[:10]:
        status_icon = {
            u"pass": u"PASS",
            u"fail": u"FAIL",
            u"warning": u"WARN",
            u"info": u"INFO",
        }.get(c["status"], u"?")
        rows.append([c["item"], status_icon, c["message"], c["standard"]])
    _print(html_table(headers, rows))
    _print(u"")

    return {
        "score": score,
        "grade": grade,
        "checks": len(checks),
        "fails": report["fail_count"],
        "warnings": report["warn_count"],
    }


def step4_drawing():
    _print(u"## 步骤 4/4：智能出图")
    _print(u"")

    from Autodesk.Revit.DB import (
        FilteredElementCollector, Level, ViewFamilyType, ViewFamily,
        ViewPlan, Transaction
    )

    levels = to_element_list(FilteredElementCollector(doc).OfClass(Level))
    levels_sorted = sorted(levels, key=lambda l: l.Elevation)

    floor_plan_type = None
    for vft in to_element_list(FilteredElementCollector(doc).OfClass(ViewFamilyType)):
        try:
            if vft.ViewFamily == ViewFamily.FloorPlan:
                floor_plan_type = vft
                break
        except Exception:
            pass

    plan_views = []
    if floor_plan_type:
        for level in levels_sorted:
            try:
                existing = FilteredElementCollector(doc).OfClass(ViewPlan)
                found = False
                for v in existing:
                    try:
                        if v.GenLevel and v.GenLevel.Id == level.Id:
                            plan_views.append(v)
                            found = True
                            break
                    except Exception:
                        pass
                if not found:
                    t = Transaction(doc, u"AI-创建平面图-%s" % level.Name)
                    t.Start()
                    vp = ViewPlan.Create(doc, floor_plan_type.Id, level.Id)
                    plan_views.append(vp)
                    t.Commit()
            except Exception:
                pass

    _print(u"平面图: %d 个已创建" % len(plan_views))
    _print(u"")

    return {"plans": len(plan_views)}


def main():
    _print(u"# AI + BIM 全流程演示")
    _print(u"")

    params, path = load_params()
    if params is None:
        try:
            from Autodesk.Revit.UI import TaskDialog, TaskDialogCommonButtons
            dlg = TaskDialog(u"一键演示")
            dlg.MainInstruction = u"未找到 building_params.json"
            dlg.MainContent = u"请将 building_params.json 放入 lib 目录"
            dlg.CommonButtons = TaskDialogCommonButtons.Ok
            dlg.Show()
        except Exception:
            pass
        return

    _print(u"参数文件: %s" % path)
    binfo = params.get("building_info", {})
    _print(u"- 建筑: **%s**" % binfo.get("building_name", u"未命名"))
    _print(u"- 层数: **%d**" % binfo.get("total_floors", 1))
    _print(u"- 尺寸: **%.1f x %.1f m**" % (
        binfo.get("footprint_length_m", 12.0),
        binfo.get("footprint_width_m", 9.0)))
    _print(u"")
    _print(u"---")
    _print(u"")

    result_model = step1_model(params)
    result_health = step2_health_check()
    result_standards = step3_standards_review()
    result_drawing = step4_drawing()

    _print(u"---")
    _print(u"")
    _print(u"# 演示完成 — 汇总报告")
    _print(u"")
    _print(u"### 建模结果")
    _print(u"- 标高: %d 个 | 外墙: %d 堵 | 内墙: %d 堵" % (
        result_model["levels"], result_model["ext_walls"], result_model["int_walls"]))
    _print(u"- 楼板: %d 块 | 屋顶: %s | 窗: %d | 门: %d | 阳台: %d" % (
        result_model["floors"],
        u"已创建" if result_model["roof"] else u"未创建",
        result_model["windows"], result_model["doors"], result_model["balconies"]))
    _print(u"")
    _print(u"### 体检结果")
    _print(u"- 健康分: **%d / 100 (%s)**" % (result_health["score"], result_health["grade"]))
    _print(u"- 构件: %d 个 | 楼层: %d 个" % (result_health["elements"], result_health["levels"]))
    _print(u"- 警告: %d | 错误: %d" % (result_health["warnings"], result_health["errors"]))
    _print(u"")
    _print(u"### 规范审查")
    _print(u"- 审查分: **%d / 100 (%s)**" % (result_standards["score"], result_standards["grade"]))
    _print(u"- 检查项: %d | 不合格: %d | 警告: %d" % (
        result_standards["checks"], result_standards["fails"], result_standards["warnings"]))
    _print(u"")
    _print(u"### 出图结果")
    _print(u"- 平面图: %d 个" % result_drawing["plans"])
    _print(u"")
    _print(u"---")
    _print(u"")
    _print(u"**全流程演示完成** — AI + BIM 自动化能力展示")
    _print(u"")
    _print(u"_BIMToolkit · 一键演示_")

    try:
        from Autodesk.Revit.UI import TaskDialog, TaskDialogCommonButtons
        dlg = TaskDialog(u"演示完成")
        dlg.MainInstruction = u"全流程演示已完成"
        dlg.MainContent = (
            u"建模: %d 个构件\n"
            u"体检: %d 分 (%s)\n"
            u"审查: %d 分 (%s)\n"
            u"出图: %d 个平面图\n\n"
            u"详细报告已在控制台输出。" % (
                result_health["elements"],
                result_health["score"], result_health["grade"],
                result_standards["score"], result_standards["grade"],
                result_drawing["plans"]))
        dlg.CommonButtons = TaskDialogCommonButtons.Ok
        dlg.Show()
    except Exception:
        pass


if __name__ == "__main__":
    main()
else:
    main()
