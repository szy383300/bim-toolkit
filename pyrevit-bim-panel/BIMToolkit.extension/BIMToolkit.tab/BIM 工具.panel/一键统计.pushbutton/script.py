# -*- coding: utf-8 -*-
u"""
一键统计 —— 项目级构件统计（只读，安全）。

输出：关键指标（构件总数/楼层/视图/图纸/族/警告）+ 按类别 Top30 分布。
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "..", "lib"))

from collections import Counter
from pyrevit import revit, DB, script
from bimlib import all_model_elements, html_table, to_element_list

doc = revit.doc
output = script.get_output()


def main():
    output.print_md(u"## 项目一键统计")

    els = to_element_list(all_model_elements(doc))
    cat = Counter(e.Category.Name if e.Category else u"(无类别)" for e in els)

    levels = to_element_list(DB.FilteredElementCollector(doc).OfClass(DB.Level).ToElements())
    views = to_element_list(DB.FilteredElementCollector(doc).OfClass(DB.View).ToElements())
    sheets = to_element_list(DB.FilteredElementCollector(doc).OfClass(DB.ViewSheet).ToElements())
    families = to_element_list(DB.FilteredElementCollector(doc).OfClass(DB.Family).ToElements())
    warnings = to_element_list(doc.GetWarnings())

    summary = [
        [u"模型构件总数", len(els)],
        [u"楼层数", len(levels)],
        [u"视图数（含图纸/明细表）", len(views)],
        [u"图纸数", len(sheets)],
        [u"已载入族数", len(families)],
        [u"当前警告数", len(warnings)],
    ]
    output.print_html(html_table([u"指标", u"数值"], summary))

    output.print_md(u"### 按类别 Top 30")
    top = cat.most_common(30)
    output.print_html(html_table([u"类别", u"数量"],
                                 [[k, v] for k, v in top]))
    output.print_md(u"_共 %d 个类别。_" % len(cat))


if __name__ == "__main__":
    main()
else:
    main()
