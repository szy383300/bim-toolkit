# -*- coding: utf-8 -*-
"""AI 智建 > 工程量统计 — 统计构件数量/面积/体积 + 简易造价估算。"""
import os
import sys

from Autodesk.Revit.DB import FilteredElementCollector

uidoc = __revit__.ActiveUIDocument
doc = uidoc.Document

HERE = os.path.dirname(__file__)
EXT = os.path.dirname(os.path.dirname(os.path.dirname(HERE)))
LIB = os.path.join(EXT, "lib")
if LIB not in sys.path:
    sys.path.insert(0, LIB)

from bimlib import to_text, to_element_list, html_table
import cost_estimator


def main():
    try:
        from pyrevit import script
        output = script.get_output()
    except Exception:
        output = None

    counts = cost_estimator.count_elements(doc)
    cost_ref = cost_estimator.load_cost_ref()
    cost_items, total = cost_estimator.estimate_cost(counts, cost_ref)

    if output:
        output.print_md(u"## 工程量统计")
        output.print_md(u"")

        headers = [u"类别", u"数量", u"面积(m2)", u"体积(m3)"]
        rows = []
        for c in counts:
            if c["count"] > 0:
                rows.append([
                    c["category"],
                    str(c["count"]),
                    u"%.1f" % c["area_sqm"],
                    u"%.2f" % c["volume_cum"],
                ])
        output.print_html(html_table(headers, rows))

        output.print_md(u"")
        output.print_md(u"## 造价估算（参考）")
        output.print_md(u"")

        cost_headers = [u"项目", u"工程量", u"单价", u"合计"]
        cost_rows = []
        for ci in cost_items:
            cost_rows.append([ci[u"项目"], ci[u"工程量"],
                              ci[u"单价"], ci[u"合计"]])
        output.print_html(html_table(cost_headers, cost_rows))

        output.print_md(u"")
        output.print_md(u"**估算总价: %.0f 元**" % total)
        output.print_md(u"")
        output.print_md(u"_注: 造价为参考值，实际以当地定额及市场价为准_")
        output.print_md(u"---")
        output.print_md(u"_AI 工程量统计 | BIMToolkit_")

    try:
        from Autodesk.Revit.UI import TaskDialog, TaskDialogCommonButtons
        total_count = sum(c["count"] for c in counts)
        dlg = TaskDialog(u"工程量统计")
        dlg.MainInstruction = u"构件总数: %d | 估算造价: %.0f 元" % (
            total_count, total)
        dlg.MainContent = u"详细清单已在 pyRevit 控制台输出。"
        dlg.CommonButtons = TaskDialogCommonButtons.Ok
        dlg.Show()
    except Exception:
        pass


if __name__ == "__main__":
    main()
else:
    main()
