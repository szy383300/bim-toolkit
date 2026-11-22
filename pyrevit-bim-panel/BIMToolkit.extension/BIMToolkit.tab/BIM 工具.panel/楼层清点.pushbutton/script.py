# -*- coding: utf-8 -*-
u"""
楼层清点 —— 按楼层统计模型构件数量（只读，安全，无需事务）。

运行：在 Revit 里点 BIMToolkit 选项卡 → BIM 工具 → 楼层清点，
结果打印到 pyRevit 输出窗口（HTML 表格）。
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "..", "lib"))

from pyrevit import revit, DB, script
from bimlib import (all_model_elements, html_table, to_element_list,
                    stats_calibration_note)

doc = revit.doc
output = script.get_output()


def main():
    output.print_md(u"## 楼层构件清点")

    # Level 本身就是实例，这里不叠类型过滤器（叠 True 等价、叠 False 会把它们全滤掉）
    levels = to_element_list(
        DB.FilteredElementCollector(doc).OfClass(DB.Level))
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

    if not levels:
        output.print_md(u"_当前项目没有楼层（Level）。_"
                        u"若项目里明明有标高，说明收集器口径出了问题，请勿采信本表。")

    output.print_html(html_table([u"楼层", u"构件数"], rows))
    _cal = stats_calibration_note(doc)
    if _cal:
        output.print_md(_cal)
    output.print_md(u"_共 %d 个楼层，%d 个模型构件。_" % (len(levels), all_n))


if __name__ == "__main__":
    main()
else:
    main()
