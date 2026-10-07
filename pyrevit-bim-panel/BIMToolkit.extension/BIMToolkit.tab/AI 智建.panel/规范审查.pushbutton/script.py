# -*- coding: utf-8 -*-
"""AI 智建 > 规范审查 — 自动检查当前模型是否符合建筑规范。

检查 5 类：防火 / 抗震 / 结构 / 节能 / 面积
输出：HTML 审查报告 + 评分 + 规则建议

2026-10-01（S13）：在规则报告之后**追加一节 AI 整改建议**。
分工是刻意的：
  - **判定**由 standards_checker 负责（确定的、可复现的、可追责的）
  - **AI 只负责解释**：把"哪条不达标"讲成人能看懂的整改路径与规范依据
  - AI 输出**不写入模型**，且失败时只少一节，**绝不影响判定结论**
"""
import os
import sys

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

# AI 解释层是**可选**的：拿不到就只用规则建议，按钮照常工作
try:
    import ai_advisor
except Exception:
    ai_advisor = None

_FALLBACK_DISCLAIMER = (u"⚠️ **以下内容由 AI 生成，仅供参考，需人工复核。**"
                        u"判定结论来自规则引擎，不受本节影响。")


def _doc_title(doc):
    try:
        return to_text(doc.Title)
    except Exception:
        return u"(未知)"


def _run_ai_advice(report, doc):
    """请求 AI 建议。返回 (文本, None) 或 (None, 失败原因)。

    任何异常都吞掉并转成"原因字符串" —— AI 是加分项，不能拖垮审查本身。
    """
    if ai_advisor is None:
        return None, u"未加载 ai_advisor 模块"
    try:
        return ai_advisor.advise(report, _doc_title(doc))
    except Exception as e:
        return None, u"AI 建议异常: %s" % to_text(e)


def show_ai_section(output, text, err):
    if not output:
        return
    output.print_md(u"")
    output.print_md(u"---")
    output.print_md(u"### AI 整改建议（仅供参考）")
    output.print_md(u"")
    if err:
        output.print_md(u"_未生成：%s_" % err)
        output.print_md(u"")
        output.print_md(u"这不影响上面的判定结论 —— 判定来自规则引擎。"
                        u"如需 AI 建议，请检查 `lib/ai_advisor_config.json` "
                        u"与环境变量 `DEEPSEEK_API_KEY`。")
        return
    disclaimer = getattr(ai_advisor, "DISCLAIMER", _FALLBACK_DISCLAIMER)
    output.print_md(disclaimer)
    output.print_md(u"")
    output.print_md(text)


def show_report(report):
    try:
        from pyrevit import script
        output = script.get_output()
    except Exception:
        output = None

    score = report["score"]
    grade = report["grade"]
    checks = report["checks"]
    feedback = report["feedback"]
    params = report["model_params"]

    if output:
        output.print_md(u"## 规范审查报告")
        output.print_md(u"")
        output.print_md(u"### 模型概况")
        output.print_md(u"- 层数: **%d**" % params.get("total_floors", 0))
        output.print_md(u"- 建筑高度: **%.1f m**" % (
            params.get("building_height_mm", 0) / 1000.0))
        output.print_md(u"- 墙厚: **%d mm**" % params.get(
            "wall_thickness_mm", 0))
        output.print_md(u"- 估算面积: **%.1f m2**" % params.get(
            "total_area_sqm", 0))
        output.print_md(u"")

        color = u"#27ae60" if score >= 75 else (
            u"#f39c12" if score >= 60 else u"#e74c3c")
        output.print_md(
            u"### 综合评分: <span style='color:%s'>**%d 分 (%s)**</span>" % (
                color, score, grade))
        output.print_md(u"")

        headers = [u"检查项", u"状态", u"说明", u"规范依据", u"建议"]
        rows = []
        for c in checks:
            status_icon = {
                u"pass": u"PASS",
                u"fail": u"FAIL",
                u"warning": u"WARN",
                u"info": u"INFO",
            }.get(c["status"], u"?")
            rows.append([
                c["item"], status_icon, c["message"],
                c["standard"], c["suggestion"]
            ])
        output.print_html(html_table(headers, rows))

        if feedback:
            output.print_md(u"")
            output.print_md(u"### 整改建议")
            for fb in feedback:
                priority = u"[高]" if fb["priority"] == u"high" else u"[中]"
                output.print_md(u"- %s **%s**: %s" % (
                    priority, fb["item"], fb["action"]))

        output.print_md(u"")
        output.print_md(u"---")
        output.print_md(u"_AI 规范审查 | BIMToolkit_")


def main():
    try:
        from pyrevit import script
        output = script.get_output()
        output.print_md(u"## 正在审查...")
    except Exception:
        output = None

    report = standards_checker.run_review(doc)

    # 1) 规则报告先出 —— 判定是权威结论，不受后面 AI 节影响
    show_report(report)

    # 2) 再追加 AI 解释节（可选、可失败、不回写模型）
    try:
        output.print_md(u"")
        output.print_md(u"_正在请求 AI 整改建议…_")
    except Exception:
        pass
    ai_text, ai_err = _run_ai_advice(report, doc)
    show_ai_section(output, ai_text, ai_err)

    if ai_err:
        ai_note = u"AI 建议: 未生成（%s）" % ai_err
    else:
        ai_note = u"AI 建议: 已生成（仅供参考，需人工复核）"

    try:
        from Autodesk.Revit.UI import TaskDialog, TaskDialogCommonButtons
        dlg = TaskDialog(u"规范审查完成")
        dlg.MainInstruction = u"审查得分: %d 分 (%s)" % (
            report["score"], report["grade"])
        dlg.MainContent = (
            u"检查项: %d | 不合格: %d | 警告: %d\n"
            u"%s\n\n"
            u"详细报告已在 pyRevit 控制台输出。" % (
                len(report["checks"]),
                report["fail_count"],
                report["warn_count"],
                ai_note))
        dlg.CommonButtons = TaskDialogCommonButtons.Ok
        dlg.Show()
    except Exception:
        pass


if __name__ == "__main__":
    main()
else:
    main()
