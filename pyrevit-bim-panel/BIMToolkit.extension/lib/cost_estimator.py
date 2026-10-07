# -*- coding: utf-8 -*-
u"""
cost_estimator.py -- 工程量统计与造价估算。

从 Revit 模型统计各类构件数量/面积/体积，结合单价参考估算造价。
IronPython 2.7 兼容。
"""
import os
import json

try:
    from pyrevit import revit, DB
except Exception:
    # Bridge/embedded context: after pyRevit command teardown the pyrevit
    # package import can fail, leaving DB=None (breaks collectors with
    # "'NoneType' object has no attribute 'FilteredElementCollector'").
    # Fall back to a direct RevitAPI reference - the same path
    # model_builder.py uses successfully inside the bridge engine.
    revit = None
    try:
        import clr
        clr.AddReference("RevitAPI")
        from Autodesk.Revit import DB
    except Exception:
        DB = None

try:
    from bimlib import to_text, to_element_list
except Exception:
    def to_text(v):
        if isinstance(v, bytes):
            try:
                return v.decode("utf-8")
            except Exception:
                return v.decode("utf-8", "replace")
        try:
            return unicode(v)
        except NameError:
            return str(v)

    def to_element_list(collector):
        try:
            return list(collector.ToElements())
        except Exception:
            return list(collector)


FT_PER_MM = 1.0 / 304.8
FT_PER_M = 1.0 / 0.3048
SQFT_PER_SQM = FT_PER_M * FT_PER_M
CUFT_PER_CUM = FT_PER_M * FT_PER_M * FT_PER_M


def load_cost_ref():
    u"""加载造价参考数据。"""
    lib_dir = os.path.dirname(os.path.abspath(__file__))
    path = os.path.join(lib_dir, "standards_quick.json")
    if os.path.exists(path):
        with open(path, "rb") as f:
            raw = f.read()
        if raw[:3] == b"\xef\xbb\xbf":
            raw = raw[3:]
        data = json.loads(raw.decode("utf-8"))
        return data.get("cost_ref", {})

    return {
        "labor_per_day": 350,
        "concrete_per_m3": 450,
        "steel_per_ton": 4200,
        "formwork_per_m2": 45,
    }


def count_elements(doc):
    u"""统计各类构件数量。返回 list of dict。"""
    categories = [
        (DB.BuiltInCategory.OST_Walls, u"墙"),
        (DB.BuiltInCategory.OST_Floors, u"楼板"),
        (DB.BuiltInCategory.OST_Doors, u"门"),
        (DB.BuiltInCategory.OST_Windows, u"窗"),
        (DB.BuiltInCategory.OST_Roofs, u"屋顶"),
        (DB.BuiltInCategory.OST_Columns, u"柱"),
        (DB.BuiltInCategory.OST_Stairs, u"楼梯"),
        (DB.BuiltInCategory.OST_Rooms, u"房间"),
    ]

    results = []
    for cat, name in categories:
        try:
            elems = to_element_list(
                DB.FilteredElementCollector(doc).OfCategory(cat)
                .WhereElementIsNotElementType())
            count = len(elems)
        except Exception:
            count = 0

        area_sqm = 0.0
        volume_cum = 0.0

        if count > 0 and cat not in (DB.BuiltInCategory.OST_Rooms,):
            try:
                elems = to_element_list(
                    DB.FilteredElementCollector(doc).OfCategory(cat)
                    .WhereElementIsNotElementType())
                for e in elems:
                    try:
                        ap = e.get_Parameter(
                            DB.BuiltInParameter.HOST_AREA_COMPUTED)
                        if ap and ap.HasValue:
                            area_sqm += ap.AsDouble() / SQFT_PER_SQM
                    except Exception:
                        pass
                    try:
                        vp = e.get_Parameter(
                            DB.BuiltInParameter.HOST_VOLUME_COMPUTED)
                        if vp and vp.HasValue:
                            volume_cum += vp.AsDouble() / CUFT_PER_CUM
                    except Exception:
                        pass
            except Exception:
                pass

        results.append({
            "category": name,
            "count": count,
            "area_sqm": round(area_sqm, 1),
            "volume_cum": round(volume_cum, 2),
        })

    return results


def estimate_cost(counts, cost_ref):
    u"""简易造价估算。"""
    concrete_per_m3 = cost_ref.get("concrete_per_m3", 450)
    formwork_per_m2 = cost_ref.get("formwork_per_m2", 45)
    steel_per_ton = cost_ref.get("steel_per_ton", 4200)

    total_concrete_cum = 0
    total_formwork_sqm = 0
    for c in counts:
        if c["category"] in (u"墙", u"楼板", u"柱", u"屋顶"):
            total_concrete_cum += c["volume_cum"]
            total_formwork_sqm += c["area_sqm"]

    concrete_cost = total_concrete_cum * concrete_per_m3
    formwork_cost = total_formwork_sqm * formwork_per_m2
    steel_estimate_ton = total_concrete_cum * 0.08
    steel_cost = steel_estimate_ton * steel_per_ton

    items = [
        {u"项目": u"混凝土", u"工程量": u"%.1f m3" % total_concrete_cum,
         u"单价": u"%d 元/m3" % concrete_per_m3,
         u"合计": u"%.0f 元" % concrete_cost},
        {u"项目": u"模板", u"工程量": u"%.1f m2" % total_formwork_sqm,
         u"单价": u"%d 元/m2" % formwork_per_m2,
         u"合计": u"%.0f 元" % formwork_cost},
        {u"项目": u"钢筋(估)", u"工程量": u"%.2f 吨" % steel_estimate_ton,
         u"单价": u"%d 元/吨" % steel_per_ton,
         u"合计": u"%.0f 元" % steel_cost},
    ]

    total = concrete_cost + formwork_cost + steel_cost
    return items, round(total, 0)
