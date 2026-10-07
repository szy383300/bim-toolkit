# -*- coding: utf-8 -*-
u"""
BIM 体检 —— 项目级模型健康体检（只读，安全）。

与「一键统计」天然衔接：首屏复用同样的顶层指标（构件/楼层/视图/图纸/族/警告），
随后展开四段深度体检：
  ① 警告/错误扫描  ② 族体检  ③ 参数化体检  ④ 综合健康分 + 整改建议

全部为只读分析，不改动模型。
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "..", "lib"))

from pyrevit import revit, DB, script
from bimlib import all_model_elements, html_table, to_element_list
from bimhealth import (scan_warnings, scan_families, scan_parameters,
                       health_score, health_grade)

doc = revit.doc
output = script.get_output()


def main():
    output.print_md(u"## BIM 体检报告")

    # ---- 顶层指标（与一键统计口径一致，便于衔接）----
    els = to_element_list(all_model_elements(doc))
    levels = to_element_list(DB.FilteredElementCollector(doc).OfClass(DB.Level))
    views = to_element_list(DB.FilteredElementCollector(doc).OfClass(DB.View))
    sheets = to_element_list(DB.FilteredElementCollector(doc).OfClass(DB.ViewSheet))

    # ---- ① 警告/错误扫描 ----
    warn = scan_warnings(doc)
    # ---- ② 族体检 ----
    fam = scan_families(doc)
    # ---- ③ 参数化体检 ----
    param = scan_parameters(doc, all_model_elements)
    # ---- ④ 健康分 ----
    score = health_score(len(els), warn["warnings"], warn["errors"],
                         len(fam["orphan_families"]), param["missing_mark"])
    grade = health_grade(score)

    # ---- 扫描异常提示（错误可见化：避免某段扫描崩了却显示成 0/空误导）----
    _scan_errors = []
    if warn.get("error"):
        _scan_errors.append((u"① 警告/错误扫描", warn["error"]))
    if fam.get("error"):
        _scan_errors.append((u"② 族体检", fam["error"]))
    if param.get("error"):
        _scan_errors.append((u"③ 参数化体检", param["error"]))
    if _scan_errors:
        output.print_md(u"### ⚠️ 扫描异常提示")
        for _sec, _msg in _scan_errors:
            output.print_md(u"_%s 执行出错，对应段落结果可能不完整：%s_" % (_sec, _msg))
        output.print_md(u"_（通常是该段用到的某个 Revit API 表面在真机才暴露、测试台未覆盖；可用「一键统计」交叉核对，或反馈维护者。）_")

    # ---- 首屏：顶层指标 ----
    summary = [
        [u"模型构件总数", len(els)],
        [u"楼层数", len(levels)],
        [u"视图数（含图纸/明细表）", len(views)],
        [u"图纸数", len(sheets)],
        [u"已载入族数", fam["families"]],
        [u"警告数", warn["warnings"]],
        [u"错误数", warn["errors"]],
    ]
    output.print_md(u"### 顶层指标（与一键统计一致）")
    output.print_html(html_table([u"指标", u"数值"], summary))

    # ---- 健康分卡片 ----
    output.print_md(u"### 综合健康分：**%d / 100** — %s" % (score, grade))
    output.print_md(u"_扣分依据：警告密度、硬性错误、孤儿族、漏标实例。_")

    # ---- ① 警告/错误扫描 ----
    output.print_md(u"###  警告 / 错误扫描")
    if warn.get("error"):
        output.print_md(u"_⚠️ 本段扫描异常：%s_" % warn["error"])
    if warn["total"] == 0:
        output.print_md(u"_无警告、无错误，干净。_")
    else:
        rows = [[d, (u"**错误**" if s == "Error" else u"警告"), c] for d, c, s in warn["top"]]
        output.print_html(html_table([u"问题描述", u"级别", u"次数"], rows))
        output.print_md(u"_受影响构件总数：**%d**（去重）。_" % len(warn["affected"]))
        if warn["errors"] > 0:
            output.print_md(u"_⚠️ 存在 **%d** 个硬性错误，建议优先处理。_" % warn["errors"])

    # ---- ② 族体检 ----
    output.print_md(u"###  族体检")
    if fam.get("error"):
        output.print_md(u"_⚠️ 本段扫描异常：%s_" % fam["error"])
    fam_rows = [
        [u"已载入族（用户/系统）", u"%d（%d / %d）" % (fam["families"], fam["user_families"], fam["system_families"])],
        [u"族类型(FamilySymbol)总数", fam["symbols"]],
        [u"可编辑族", fam["editable_families"]],
        [u"共享族", fam["shared_families"]],
        [u"孤儿族（无类型）", len(fam["orphan_families"])],
    ]
    output.print_html(html_table([u"检查项", u"结果"], fam_rows))
    if fam["orphan_families"]:
        shown = u"、".join(fam["orphan_families"][:10])
        more = u" …" if len(fam["orphan_families"]) > 10 else ""
        output.print_md(u"_孤儿族：%s%s（无族类型，建议清理或补全）。_" % (shown, more))

    # ---- ③ 参数化体检 ----
    output.print_md(u"### ③ 参数化体检")
    if param.get("error"):
        output.print_md(u"_⚠️ 本段扫描异常：%s_" % param["error"])
    min_name, min_c = param["min_params_type"]
    param_rows = [
        [u"族类型平均参数个数", param["avg_type_params"]],
        [u"参数最少的族类型", u"%s（%d 个参数）" % (min_name or u"—", min_c if min_c != 10 ** 9 else 0)],
        [u"参与标记检查的实例数", param["total_checked"]],
        [u"「标记(Mark)」为空的实例", param["missing_mark"]],
    ]
    output.print_html(html_table([u"检查项", u"结果"], param_rows))
    if param["total_checked"]:
        rate = param["missing_mark"] / param["total_checked"] * 100.0
        output.print_md(u"_漏标率：**%.1f%%**。_" % rate)

    # ---- ④ 整改建议 ----
    output.print_md(u"### ④ 整改建议")
    advice = []
    if warn["errors"] > 0:
        advice.append(u"优先清除 **%d** 个硬性错误（见①中 Error 行）。" % warn["errors"])
    if warn["warnings"] > 20:
        advice.append(u"警告偏多（%d 条），按频次①逐项清理可显著降低模型错误风险。" % warn["warnings"])
    if fam["orphan_families"]:
        advice.append(u"清理 %d 个孤儿族（无类型，徒增文件体积）。" % len(fam["orphan_families"]))
    if param["min_params_type"][1] <= 1:
        advice.append(u"族类型「%s」参数极少，疑似未参数化，检查其驱动方式。" % min_name)
    if param["missing_mark"] > 0:
        advice.append(u"补齐 %d 个实例的「标记」，便于后续 4D 关联与工程量核对。" % param["missing_mark"])
    if not advice:
        advice.append(u"模型状态良好，未发现明显风险点。")
    for i, a in enumerate(advice, 1):
        output.print_md("%d. %s" % (i, a))

    output.print_md(u"\n_— BIMToolkit · BIM 体检（只读）—_")


if __name__ == "__main__":
    main()
else:
    main()
