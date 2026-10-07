# -*- coding: utf-8 -*-
u"""
构件清单导出 —— 把全部模型构件导出为 CSV / JSON / MD（只读，安全）。

列为：ElementId, Category, FamilyType, Level, Name。
这是「进度(task.uid) ↔ Revit 构件(ElementId)」4D 关联的底表：
后续阶段 5 可把本表与 schedule_summary.json 按楼层/类别对齐，做 4D 推进。

导出：弹出保存框选路径（取消则落到脚本同目录默认名）。同时产出：
  - .csv   人读/Excel
  - .json  供阶段5 程序化读取（每行为一个构件 dict）
  - .md    简易报告（前 200 行）
"""
import sys, os, json
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "..", "lib"))

from pyrevit import revit, DB, script
from Autodesk.Revit.UI import TaskDialog, TaskDialogCommonButtons
from bimlib import (all_model_elements, element_level_name, family_type_name,
                    write_csv_unicode, to_element_list)

doc = revit.doc
output = script.get_output()


def _msg_box(msg):
    dlg = TaskDialog(u"构件清单导出")
    dlg.MainInstruction = msg
    dlg.CommonButtons = TaskDialogCommonButtons.Ok
    dlg.Show()


headers = ["ElementId", "Category", "FamilyType", "Level", "Name"]
rows = []
for e in to_element_list(all_model_elements(doc)):
    name = ""
    try:
        name = e.Name
    except Exception:
        name = ""
    rows.append([
        e.Id.IntegerValue,
        e.Category.Name if e.Category else "",
        family_type_name(e),
        element_level_name(e),
        name,
    ])

n = len(rows)

# 让用户选保存位置（取消则落到脚本同目录默认名）
# Use WinForms SaveFileDialog instead of forms.save_file
import clr
clr.AddReference("System.Windows.Forms")
from System.Windows.Forms import SaveFileDialog, DialogResult

sfd = SaveFileDialog()
sfd.Title = u"保存构件清单"
sfd.FileName = "elements_export.csv"
sfd.Filter = "CSV files (*.csv)|*.csv|All files (*.*)|*.*"
result = sfd.ShowDialog()
if result == DialogResult.OK:
    out_csv = sfd.FileName
else:
    out_csv = os.path.join(os.path.dirname(__file__), "elements_export.csv")

# CSV（复用兼容 helper，UTF-8 BOM）
write_csv_unicode(out_csv, headers, rows)

# JSON（供阶段5 程序化读取；用字节写规避 IronPython open(encoding=) 限制）
json_path = os.path.splitext(out_csv)[0] + ".json"
json_bytes = json.dumps(
    [dict(zip(headers, r)) for r in rows],
    ensure_ascii=False, indent=2).encode("utf-8")
open(json_path, "wb").write(json_bytes)

# MD 报告（前 200 行）
md_path = os.path.splitext(out_csv)[0] + ".md"
md = u"# 构件清单导出\n\n共 **%d** 个构件。\n\n" % n
md += "| " + " | ".join(headers) + " |\n"
md += "|" + "|".join(["---"] * len(headers)) + "|\n"
for r in rows[:200]:
    md += "| " + " | ".join("" if c is None else str(c) for c in r) + " |\n"
if n > 200:
    md += u"\n_（仅显示前 200 行，完整数据见 CSV / JSON）_\n"
open(md_path, "wb").write(md.encode("utf-8"))

output.print_md(u"## 构件清单导出")
output.print_md(u"已导出 **%d** 个构件：" % n)
output.print_md(u"- CSV：`%s`" % out_csv)
output.print_md(u"- JSON：`%s`" % json_path)
output.print_md(u"- MD 报告：`%s`" % md_path)
_msg_box(u"已导出 %d 个构件：\n%s\n%s\n%s" % (n, out_csv, json_path, md_path))
