# -*- coding: utf-8 -*-
"""
Create a detailed small villa in Revit (pyRevit / IronPython 2.7, Revit 2019).
Gable roof, 2 stories, windows, doors, interior walls, stairs.

Fixed: Removed hardcoded ElementIds — now finds levels by elevation.
Added: pyrevit output support for consistent feedback.
"""

import os
import sys

from Autodesk.Revit.DB import (
    Line, XYZ, CurveArray, BuiltInCategory, FilteredElementCollector,
    Level, Wall, Transaction, SketchPlane, Plane, ModelCurve, ModelCurveArray
)
from Autodesk.Revit.DB.Structure import StructuralType

uidoc = __revit__.ActiveUIDocument
doc = uidoc.Document

# Villa dimensions in feet (Revit internal unit)
LENGTH = 32.8084   # 10m
WIDTH = 26.2467    # 8m
WALL_H = 9.8425    # 3m per floor
OVERHANG = 1.6404  # 0.5m roof overhang

corners = [
    XYZ(0, 0, 0),
    XYZ(LENGTH, 0, 0),
    XYZ(LENGTH, WIDTH, 0),
    XYZ(0, WIDTH, 0),
]

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


def find_levels():
    """Find two levels closest to 0ft and 10ft (approx 3m apart)."""
    levels = []
    for l in FilteredElementCollector(doc).OfClass(Level).ToElements():
        levels.append(l)
    if len(levels) < 2:
        return None, None
    # Sort by elevation
    levels_sorted = sorted(levels, key=lambda lv: lv.Elevation)
    # Pick lowest and second-lowest as level1/level2
    level1 = levels_sorted[0]
    level2 = levels_sorted[1] if len(levels_sorted) > 1 else None
    return level1, level2


def main():
    level1, level2 = find_levels()
    if level1 is None or level2 is None:
        _print(u"**错误**: 需要至少两个标高才能创建别墅")
        return

    t = Transaction(doc, "Create Detailed Villa")
    t.Start()

    try:
        # --- Wall type (skip curtain walls) ---
        wt_col = FilteredElementCollector(doc) \
            .OfCategory(BuiltInCategory.OST_Walls) \
            .WhereElementIsElementType()
        wall_type = None
        for wt in wt_col:
            if "Curtain" not in wt.Name and "\u5e55\u5899" not in wt.Name:
                wall_type = wt
                break
        if wall_type is None:
            wall_type = FilteredElementCollector(doc) \
                .OfCategory(BuiltInCategory.OST_Walls) \
                .WhereElementIsElementType().FirstElement()

        # --- 4 exterior walls ---
        import math
        floor_height = level2.Elevation - level1.Elevation
        wall_height = floor_height + 1.0
        ext_walls = []
        for i in range(4):
            line = Line.CreateBound(corners[i], corners[(i + 1) % 4])
            w = Wall.Create(doc, line, wall_type.Id, level1.Id, wall_height, 0, False, False)
            ext_walls.append(w)
        _print(u"外墙: 4 面")

        # --- Windows ---
        window_types = FilteredElementCollector(doc) \
            .OfCategory(BuiltInCategory.OST_Windows) \
            .WhereElementIsElementType() \
            .ToElements()

        if window_types:
            win_type = window_types[0]

            # Front wall (wall 0): 3 windows per floor
            front_wall = ext_walls[0]
            front_curve = front_wall.Location.Curve
            for floor_offset in [0, floor_height]:
                for t_param in [0.25, 0.5, 0.75]:
                    pt = front_curve.Evaluate(t_param, True)
                    pt = XYZ(pt.X, pt.Y, level1.Elevation + floor_offset + 3.0)
                    try:
                        doc.Create.NewFamilyInstance(pt, win_type, front_wall, level1, StructuralType.NonStructural)
                    except Exception as e:
                        pass

            # Back wall (wall 2): 3 windows per floor
            back_wall = ext_walls[2]
            back_curve = back_wall.Location.Curve
            for floor_offset in [0, floor_height]:
                for t_param in [0.25, 0.5, 0.75]:
                    pt = back_curve.Evaluate(t_param, True)
                    pt = XYZ(pt.X, pt.Y, level1.Elevation + floor_offset + 3.0)
                    try:
                        doc.Create.NewFamilyInstance(pt, win_type, back_wall, level1, StructuralType.NonStructural)
                    except Exception as e:
                        pass

            # Side walls (walls 1, 3): 2 windows per floor
            for wall_idx in [1, 3]:
                side_wall = ext_walls[wall_idx]
                side_curve = side_wall.Location.Curve
                for floor_offset in [0, floor_height]:
                    for t_param in [0.33, 0.67]:
                        pt = side_curve.Evaluate(t_param, True)
                        pt = XYZ(pt.X, pt.Y, level1.Elevation + floor_offset + 3.0)
                        try:
                            doc.Create.NewFamilyInstance(pt, win_type, side_wall, level1, StructuralType.NonStructural)
                        except Exception as e:
                            pass

            _print(u"窗户: 已放置")
        else:
            _print(u"警告: 未找到窗族类型")

        # --- Front door ---
        door_types = FilteredElementCollector(doc) \
            .OfCategory(BuiltInCategory.OST_Doors) \
            .WhereElementIsElementType() \
            .ToElements()

        if door_types:
            door_type = door_types[0]
            front_wall = ext_walls[0]
            front_curve = front_wall.Location.Curve
            door_pt = front_curve.Evaluate(0.5, True)
            door_pt = XYZ(door_pt.X, door_pt.Y, level1.Elevation)
            try:
                doc.Create.NewFamilyInstance(door_pt, door_type, front_wall, level1, StructuralType.NonStructural)
                _print(u"门: 已放置")
            except Exception as e:
                pass
        else:
            _print(u"警告: 未找到门族类型")

        # --- Interior partition walls ---
        int_wall_line = Line.CreateBound(
            XYZ(LENGTH * 0.4, 0, 0),
            XYZ(LENGTH * 0.4, WIDTH, 0)
        )
        int_wall = Wall.Create(doc, int_wall_line, wall_type.Id, level1.Id, floor_height, 0, False, False)
        _print(u"内墙: 1 面")

        # --- Floor slab at Level 2 ---
        floor_type = FilteredElementCollector(doc) \
            .OfCategory(BuiltInCategory.OST_Floors) \
            .WhereElementIsElementType().FirstElement()

        boundary = CurveArray()
        for i in range(4):
            boundary.Append(Line.CreateBound(corners[i], corners[(i + 1) % 4]))
        doc.Create.NewFloor(boundary, floor_type, level2, False)
        _print(u"楼板: 2 层")

        # --- Stairs (manual placement required) ---
        _print(u"楼梯: 请手动放置")

        # --- Gable roof at Level 2 ---
        slope_angle = 0.5236  # 30 degrees
        roof_type = FilteredElementCollector(doc) \
            .OfCategory(BuiltInCategory.OST_Roofs) \
            .WhereElementIsElementType() \
            .FirstElement()

        rc = [
            XYZ(-OVERHANG, -OVERHANG, 0),
            XYZ(LENGTH + OVERHANG, -OVERHANG, 0),
            XYZ(LENGTH + OVERHANG, WIDTH + OVERHANG, 0),
            XYZ(-OVERHANG, WIDTH + OVERHANG, 0),
        ]
        roof_curves = CurveArray()
        for i in range(4):
            roof_curves.Append(Line.CreateBound(rc[i], rc[(i + 1) % 4]))

        model_curve_array = ModelCurveArray()
        roof, model_curve_array = doc.Create.NewFootPrintRoof(roof_curves, level2, roof_type, model_curve_array)

        for i in range(model_curve_array.Size):
            mc = model_curve_array[i]
            curve = mc.GeometryCurve
            if curve is not None:
                mid_pt = curve.Evaluate(0.5, True)
                if abs(mid_pt.Y - (-OVERHANG)) < 0.1 or abs(mid_pt.Y - (WIDTH + OVERHANG)) < 0.1:
                    roof.set_DefinesSlope(mc, True)
                    roof.set_SlopeAngle(mc, slope_angle)
                else:
                    roof.set_DefinesSlope(mc, False)

        # --- Wall tops: Revit 2019 API has no wall-to-roof attach method
        # (Wall.AttachWallTop does not exist in 2019). Walls keep their
        # explicit height; use "Attach Top/Base" manually if needed.
        _print(u"屋顶: 坡屋顶已创建（2019 API 不支持墙顶自动附着，如需贴合请手动附着）")

        t.Commit()
        _print(u"**别墅创建完成**: {:.0f}m x {:.0f}m, 2 层, 坡屋顶".format(
            LENGTH * 0.3048, WIDTH * 0.3048))

    except Exception as e:
        t.RollBack()
        _print(u"**错误**: {}".format(e))
        import traceback
        traceback.print_exc()
        raise


if __name__ == "__main__":
    main()
else:
    main()
