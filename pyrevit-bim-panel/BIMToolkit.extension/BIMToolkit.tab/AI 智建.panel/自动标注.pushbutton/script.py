# -*- coding: utf-8 -*-
"""AI 智建 > 自动标注 — 为当前视图中的墙/门/窗自动添加尺寸标注。

功能：
  1. 收集当前视图中的墙、门、窗构件
  2. 按构件位置生成线性尺寸标注（墙长度、门窗宽度）
  3. 标注结果按类别分组，便于核对

IronPython 2.7 / Revit 2019 兼容：
  - 不使用 f-string
  - 所有 Revit API 调用包裹 try/except
  - 单位转换 mm / 304.8
"""
import os
import sys

from Autodesk.Revit.DB import (
    FilteredElementCollector, Wall, FamilyInstance,
    Transaction, Reference, ReferenceArray, XYZ, Line, Dimension,
    BuiltInCategory
)

uidoc = __revit__.ActiveUIDocument
doc = uidoc.Document
active_view = doc.ActiveView

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


def _get_element_refs(elements):
    u"""提取构件的 Reference 列表（用于标注）。"""
    refs = []
    for el in elements:
        try:
            ref = Reference(el)
            refs.append(ref)
        except Exception:
            pass
    return refs


def _get_element_line(el):
    u"""获取构件的位置线（墙取 LocationCurve，构件取 LocationPoint）。"""
    try:
        loc = el.Location
        if loc is None:
            return None
        try:
            curve = loc.Curve
            return curve
        except Exception:
            pass
        try:
            pt = loc.Point
            return pt
        except Exception:
            pass
    except Exception:
        pass
    return None


def _create_dimension_for_walls(walls, view):
    u"""为一组墙创建线性尺寸标注。

    策略：取每面墙的 Reference（实测解析为墙【起点平面】），
    在墙的上方创建一组线性标注，显示各墙起点间距（共线首尾相接
    墙 = 各墙段实长）。

    已知限制（Revit API 实测）：
      - N 面墙得 N-1 段，末段墙长不在链内（缺末墙终点引用）；
      - Location.Curve.GetEndPointReference 返回 None（位置线无引用）；
      - 几何端面 Reference 与元素 Reference 混排会污染整条标注
        （所有段变为 -304.8mm）；设 Options.View 后端面大面积缺失。
        故端面补链方案不可行，保持元素引用链。
    """
    if len(walls) < 2:
        return 0

    refs = []
    for w in walls:
        try:
            curve = w.Location.Curve
            p0 = curve.GetEndPoint(0)
            p1 = curve.GetEndPoint(1)
            refs.append((w, p0, p1, curve))
        except Exception:
            pass

    if len(refs) < 2:
        return 0

    refs_sorted = sorted(refs, key=lambda r: min(r[1].X, r[2].X))

    # NewDimension 第三参必须是 .NET ReferenceArray（IronPython 不做 list 隐式转换）
    line_refs = ReferenceArray()
    for w, p0, p1, curve in refs_sorted:
        try:
            line_refs.Append(Reference(w))
        except Exception:
            pass

    if line_refs.Size < 2:
        return 0

    count = 0
    try:
        first_curve = refs_sorted[0][3]
        last_curve = refs_sorted[-1][3]
        p_start = first_curve.GetEndPoint(0)
        x_end = max(last_curve.GetEndPoint(0).X,
                    last_curve.GetEndPoint(1).X)

        offset_ft = 1000 / 304.8
        line_start = XYZ(p_start.X, p_start.Y + offset_ft, 0)
        line_end = XYZ(x_end, p_start.Y + offset_ft, 0)
        dim_line = Line.CreateBound(line_start, line_end)

        dim = doc.Create.NewDimension(view, dim_line, line_refs)
        if dim is not None:
            count += 1
    except Exception as e:
        _print(u"  墙标注创建失败: %s" % to_text(e))

    return count


def _create_dimension_for_instances(instances, view, category_name):
    u"""为门/窗等构件创建标注（按 X 坐标排列，标注间距）。"""
    if len(instances) < 2:
        return 0

    pts = []
    for inst in instances:
        try:
            loc = inst.Location
            if loc and loc.Point:
                pts.append((inst, loc.Point))
        except Exception:
            pass

    if len(pts) < 2:
        return 0

    pts_sorted = sorted(pts, key=lambda p: p[1].X)

    refs = ReferenceArray()
    for inst, pt in pts_sorted:
        try:
            refs.Append(Reference(inst))
        except Exception:
            pass

    if refs.Size < 2:
        return 0

    count = 0
    try:
        p0 = pts_sorted[0][1]
        p1 = pts_sorted[-1][1]
        offset_ft = 500 / 304.8
        line_start = XYZ(p0.X, p0.Y + offset_ft, p0.Z)
        line_end = XYZ(p1.X, p1.Y + offset_ft, p1.Z)
        dim_line = Line.CreateBound(line_start, line_end)

        dim = doc.Create.NewDimension(view, dim_line, refs)
        if dim is not None:
            count += 1
    except Exception as e:
        _print(u"  %s标注创建失败: %s" % (category_name, to_text(e)))

    return count


def main():
    _print(u"## AI 自动标注")
    _print(u"")
    _print(u"**当前视图**: %s" % to_text(active_view.Name))
    _print(u"")

    walls = to_element_list(
        FilteredElementCollector(doc, active_view.Id).OfClass(Wall))
    doors = to_element_list(
        FilteredElementCollector(doc, active_view.Id).OfClass(FamilyInstance).OfCategory(BuiltInCategory.OST_Doors))
    windows = to_element_list(
        FilteredElementCollector(doc, active_view.Id).OfClass(FamilyInstance).OfCategory(BuiltInCategory.OST_Windows))

    _print(u"检测到构件:")
    _print(u"  - 墙: %d 面" % len(walls))
    _print(u"  - 门: %d 个" % len(doors))
    _print(u"  - 窗: %d 个" % len(windows))
    _print(u"")

    if not walls and not doors and not windows:
        _print(u"**提示**: 当前视图无可用构件，请切换到包含构件的平面视图。")
        return

    total_dims = 0

    if walls:
        _print(u"### 1/3 标注墙体...")
        t = Transaction(doc, u"AI-标注墙体")
        t.Start()
        try:
            n = _create_dimension_for_walls(walls, active_view)
            total_dims += n
            t.Commit()
            _print(u"   墙体标注: %d 组" % n)
        except Exception as e:
            t.RollBack()
            _print(u"   墙体标注失败: %s" % to_text(e))

    if doors:
        _print(u"### 2/3 标注门...")
        t = Transaction(doc, u"AI-标注门")
        t.Start()
        try:
            n = _create_dimension_for_instances(doors, active_view, u"门")
            total_dims += n
            t.Commit()
            _print(u"   门标注: %d 组" % n)
        except Exception as e:
            t.RollBack()
            _print(u"   门标注失败: %s" % to_text(e))

    if windows:
        _print(u"### 3/3 标注窗...")
        t = Transaction(doc, u"AI-标注窗")
        t.Start()
        try:
            n = _create_dimension_for_instances(windows, active_view, u"窗")
            total_dims += n
            t.Commit()
            _print(u"   窗标注: %d 组" % n)
        except Exception as e:
            t.RollBack()
            _print(u"   窗标注失败: %s" % to_text(e))

    _print(u"")
    _print(u"---")
    _print(u"**自动标注完成** — 共 %d 组标注" % total_dims)
    _print(u"")
    _print(u"_提示: 标注位置基于构件坐标自动计算，可在视图中手动微调_")

    try:
        from Autodesk.Revit.UI import TaskDialog, TaskDialogCommonButtons
        dlg = TaskDialog(u"自动标注")
        dlg.MainInstruction = u"已创建 %d 组尺寸标注" % total_dims
        dlg.MainContent = u"墙 %d 面 / 门 %d 个 / 窗 %d 个" % (
            len(walls), len(doors), len(windows))
        dlg.CommonButtons = TaskDialogCommonButtons.Ok
        dlg.Show()
    except Exception:
        pass


if __name__ == "__main__":
    main()
else:
    main()
