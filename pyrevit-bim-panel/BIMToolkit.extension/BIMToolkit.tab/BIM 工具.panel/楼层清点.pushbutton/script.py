# -*- coding: utf-8 -*-
u"""
楼层清点 —— 按楼层统计模型构件数量（只读，安全，无需事务）。

运行：在 Revit 里点 BIMToolkit 选项卡 → BIM 工具 → 楼层清点，
结果打印到 pyRevit 输出窗口（HTML 表格）。
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "..", "lib"))

from pyrevit import revit, DB, script
from bimlib import all_model_elements, html_table, to_element_list

doc = revit.doc
output = script.get_output()


def main():
    output.print_md(u"## 楼层构件清点")

    levels = to_element_list(
        DB.FilteredElementCollector(doc).OfClass(DB.Level)
        .WherePasses(DB.ElementIsElementTypeFilter(True)))
    levels.sort(key=lambda l: l.Elevation)

    rows = []
    lv_total = 0
    for lv in levels:
        n = len(to_element_list(
            DB.FilteredElementCollector(doc)
            .WherePasses(DB.ElementLevelFilter(lv.Id))
            .WherePasses(DB.ElementIsElementTypeFilter(True))))
        rows.append([lv.Name, n])
        lv_total += n

    all_n = len(to_element_list(all_model_elements(doc)))
    rows.append([u"（无关联楼层）", all_n - lv_total])
    rows.append([u"合计（全部模型构件）", all_n])

    output.print_html(html_table([u"楼层", u"构件数"], rows))
    output.print_md(u"_共 %d 个楼层，%d 个模型构件。_" % (len(levels), all_n))


if __name__ == "__main__":
    main()
else:
    main()
