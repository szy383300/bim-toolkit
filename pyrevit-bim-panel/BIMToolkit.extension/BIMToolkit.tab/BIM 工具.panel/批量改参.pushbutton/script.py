# -*- coding: utf-8 -*-
u"""
批量改参 —— 按 CSV 批量修改构件参数。

安全（硬规矩）：
  - 默认只预览「原值 → 新值」，绝不写入。
  - 预览后弹出确认框，点「确定」才真正写入（事务包裹，可 Ctrl+Z 撤销）。无需改源码。
  - 强烈建议先在副本/测试项目试跑。

CSV 格式（UTF-8，首行表头，列顺序任意）：
  Category,Type,ParamName,Value
  - Category：类别名（如 墙、楼板、管件）；留空 = 任意类别
  - Type    ：族:类型 子串（如 '基本墙'）；留空 = 任意类型
  - ParamName：用户参数名 或 BuiltInParameter 枚举名（如 ALL_MODEL_INSTANCE_COMMENTS / 标记）
  - Value   ：新值（数字/文字；ElementId 类参数填整数 Id）
CSV 默认与本脚本同目录，文件名 batch_params.csv。
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "..", "lib"))

from pyrevit import revit, DB, script
from Autodesk.Revit.UI import TaskDialog, TaskDialogCommonButtons, TaskDialogResult
from bimlib import (all_model_elements, lookup_param, read_param,
                     set_param, html_table, family_type_name, read_csv_unicode,
                     to_element_list)

doc = revit.doc
output = script.get_output()
csv_path = os.path.join(os.path.dirname(__file__), "batch_params.csv")


def _msg_box(msg):
    dlg = TaskDialog(u"批量改参")
    dlg.MainInstruction = msg
    dlg.CommonButtons = TaskDialogCommonButtons.Ok
    dlg.Show()


def main():
    if not os.path.isfile(csv_path):
        _msg_box(u"未找到批处理 CSV：\n%s\n\n本按钮目录下需有 batch_params.csv（仓库已附带模板，"
                 u"按列 Category,Type,ParamName,Value 填好你的数据后重试）。" % csv_path)
        return

    rows = read_csv_unicode(csv_path)

    # 预匹配：把每一行 CSV 展开成 (构件, 参数名, 新值) 计划
    plan = []
    for r in rows:
        cat_r = (r.get("Category") or "").strip()
        type_r = (r.get("Type") or "").strip()
        pname = (r.get("ParamName") or "").strip()
        val = (r.get("Value") or "").strip()
        if not pname:
            continue
        for e in to_element_list(all_model_elements(doc)):
            if cat_r and (e.Category.Name if e.Category else "") != cat_r:
                continue
            if type_r and type_r.lower() not in family_type_name(e).lower():
                continue
            plan.append((e, pname, val))

    output.print_md(u"## 批量改参（**仅预览，未写入**）")
    output.print_md(u"_匹配到 %d 处参数修改计划。_" % len(plan))

    table_rows = []
    changed = 0
    for e, pname, val in plan:
        p = lookup_param(e, pname)
        old = read_param(p) if p is not None else u"(无此参数)"
        table_rows.append([e.Id.IntegerValue,
                           (e.Category.Name if e.Category else ""),
                           pname, old, val])

    if not plan:
        output.print_md(u"_未匹配到任何构件。请检查 CSV 的 Category/Type/ParamName 是否写对，"
                        u"且 batch_params.csv 与本脚本同目录。_")

    # 写入闸门：默认只预览；确认框点「确定」才真正写入（无需改源码）
    if plan:
        dlg = TaskDialog(u"批量改参")
        dlg.MainInstruction = u"即将按以上预览写入 %d 处参数到模型（Revit 可 Ctrl+Z 撤销）。" % len(plan)
        dlg.MainContent = u"确认写入？"
        dlg.CommonButtons = TaskDialogCommonButtons.Ok | TaskDialogCommonButtons.Cancel
        result = dlg.Show()
        if result == TaskDialogResult.Ok:
            with revit.Transaction(u"BIMToolkit 批量改参"):
                for e, pname, val in plan:
                    # set_param 返回 (old, new, ok, note) 四元组
                    old, new, ok, note = set_param(e, pname, val)
                    if ok:
                        changed += 1
                    else:
                        output.print_md(u"_⚠️ 写入失败（ElementId=%s, 参数=%s）：%s_" %
                                        (e.Id.IntegerValue, pname, note))
            output.print_md(u"已写入 **%d** 个参数（Revit 可 Ctrl+Z 撤销）。" % changed)

    output.print_html(html_table(
        ["ElementId", u"类别", u"参数", u"原值", u"新值"], table_rows))


if __name__ == "__main__":
    main()
else:
    main()
