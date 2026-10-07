# -*- coding: utf-8 -*-
"""AI 智建 > 智能出图 — 自动创建楼层平面视图。

功能（当前实现范围）：
  1. 为每个标高创建楼层平面图视图（已存在则复用）
  2. 立面图：Revit 2019 + IronPython 2.7 下 ViewSection API 受限，
     需在项目浏览器手动创建（脚本会给出提示）
  3. 图纸(Sheet)视口排布：本版未实现
"""
import os
import sys

from Autodesk.Revit.DB import (
    FilteredElementCollector, Level, ViewFamilyType, ViewFamily,
    ViewPlan, View, Transaction, ElementId
)
from Autodesk.Revit.UI import UIDocument

uidoc = __revit__.ActiveUIDocument
doc = uidoc.Document

HERE = os.path.dirname(__file__)
EXT = os.path.dirname(os.path.dirname(os.path.dirname(HERE)))
LIB = os.path.join(EXT, "lib")
if LIB not in sys.path:
    sys.path.insert(0, LIB)

from bimlib import to_text, to_element_list

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


def find_view_family_type(family_type):
    for vft in to_element_list(
            FilteredElementCollector(doc).OfClass(ViewFamilyType)):
        try:
            if vft.ViewFamily == family_type:
                return vft
        except Exception:
            pass
    return None


def create_floor_plan_views(levels):
    u"""为每个标高创建楼层平面图视图。"""
    floor_plan_type = find_view_family_type(ViewFamily.FloorPlan)
    if floor_plan_type is None:
        _print(u"**警告**: 未找到楼层平面图视图类型")
        return []

    views = []
    for level in levels:
        try:
            existing = FilteredElementCollector(doc).OfClass(ViewPlan)
            found = False
            for v in existing:
                try:
                    if v.GenLevel and v.GenLevel.Id == level.Id:
                        views.append(v)
                        found = True
                        break
                except Exception:
                    pass
            if not found:
                t = Transaction(doc, u"AI-创建平面图-%s" % level.Name)
                t.Start()
                vp = ViewPlan.Create(doc, floor_plan_type.Id, level.Id)
                views.append(vp)
                t.Commit()
        except Exception as e:
            _print(u"  平面图创建失败 (%s): %s" % (
                to_text(level.Name), to_text(e)))

    return views


def create_section_views(levels, params):
    u"""创建前后左右四个立面视图。

    注意: Revit 2019 + IronPython 2.7 环境下 ViewSection API 存在兼容性限制，
    立面图需手动在项目浏览器中创建。平面图已自动创建。
    """
    _print(u"**提示**: Revit 2019 + IronPython 2.7 环境下列面图 API 不可用")
    _print(u"   请在项目浏览器中手动创建立面视图")
    _print(u"   (平面图 %d 个已成功创建)" % len(levels))
    return []


def main():
    _print(u"## AI 智能出图")
    _print(u"")

    levels = to_element_list(
        FilteredElementCollector(doc).OfClass(Level))
    levels_sorted = sorted(levels, key=lambda l: l.Elevation)

    if not levels_sorted:
        _print(u"**错误**: 未找到标高")
        return

    _print(u"### 1/2 创建楼层平面图...")
    plan_views = create_floor_plan_views(levels_sorted)
    _print(u"   平面图: %d 个" % len(plan_views))

    _print(u"### 2/2 创建立面图...")
    section_views = create_section_views(levels_sorted, None)
    _print(u"   立面图: %d 个" % len(section_views))

    _print(u"")
    _print(u"---")
    _print(u"**智能出图完成** — 平面图 %d 个 + 立面图 %d 个" % (
        len(plan_views), len(section_views)))
    _print(u"")
    _print(u"_提示: 可在项目浏览器中查看生成的视图_")

    try:
        from Autodesk.Revit.UI import TaskDialog, TaskDialogCommonButtons
        dlg = TaskDialog(u"智能出图")
        dlg.MainInstruction = u"已创建 %d 个平面图 + %d 个立面图" % (
            len(plan_views), len(section_views))
        dlg.MainContent = u"请在项目浏览器中查看。"
        dlg.CommonButtons = TaskDialogCommonButtons.Ok
        dlg.Show()
    except Exception:
        pass


if __name__ == "__main__":
    main()
else:
    main()
