# -*- coding: utf-8 -*-
u"""
standards_checker.py -- 规范审查引擎。

从当前 Revit 模型提取关键参数，对照规范数据库做 5 类检查：
  1. 防火 (GB50016)
  2. 抗震 (GB50011)
  3. 节能 (地方标准)
  4. 结构 (GB50010/GB50003/GB50017)
  5. 面积 (GBT50353)

IronPython 2.7 兼容：无 f-string，unicode 安全。
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


def mm(ft_val):
    return ft_val / FT_PER_MM


def extract_model_params(doc):
    u"""从当前模型提取关键参数，返回 dict。"""
    params = {
        "total_floors": 0,
        "floor_heights_mm": [],
        "building_height_mm": 0,
        "wall_thickness_mm": 0,
        "structural_type": u"",
        "has_fire_rating": False,
        "window_area_ratio": 0.0,
        "total_area_sqm": 0.0,
        "location": u"",
    }

    levels = to_element_list(
        DB.FilteredElementCollector(doc).OfClass(DB.Level))
    params["total_floors"] = max(0, len(levels) - 1)

    heights = []
    sorted_levels = sorted(levels, key=lambda l: l.Elevation)
    for i in range(1, len(sorted_levels)):
        h_mm = mm(sorted_levels[i].Elevation - sorted_levels[i-1].Elevation)
        heights.append(round(h_mm))
    params["floor_heights_mm"] = heights

    if sorted_levels:
        top_elev = sorted_levels[-1].Elevation
        base_elev = sorted_levels[0].Elevation
        params["building_height_mm"] = round(mm(top_elev - base_elev))

    walls = to_element_list(
        DB.FilteredElementCollector(doc).OfClass(DB.Wall)
        .WhereElementIsNotElementType())
    if walls:
        try:
            w = walls[0]
            wt = doc.GetElement(w.WallType.Id)
            if wt:
                # 墙厚取类型总宽 wt.Width（此前误取"单层最厚值"，
                # 多构造层墙 200=100+50+50 会被读成 100 导致误判）
                try:
                    total_mm = round(mm(wt.Width))
                except Exception:
                    total_mm = 0
                if total_mm <= 0:
                    cs = wt.GetCompoundStructure()
                    if cs:
                        total_mm = round(sum(
                            mm(cs.GetLayerWidth(i))
                            for i in range(cs.LayerCount)))
                if total_mm > params["wall_thickness_mm"]:
                    params["wall_thickness_mm"] = total_mm
        except Exception:
            pass

    for w in walls:
        try:
            p = w.get_Parameter(
                DB.BuiltInParameter.FIRE_RATING_PARAM)
            if p and p.HasValue:
                v = p.AsString()
                if v and v.strip():
                    params["has_fire_rating"] = True
                    break
        except Exception:
            pass

    length_mm = 0
    width_mm = 0
    try:
        if walls:
            xs, ys = [], []
            for w in walls[:20]:
                loc = w.Location
                if hasattr(loc, 'Curve'):
                    c = loc.Curve
                    p0 = c.GetEndPoint(0)
                    p1 = c.GetEndPoint(1)
                    xs.extend([mm(p0.X), mm(p1.X)])
                    ys.extend([mm(p0.Y), mm(p1.Y)])
            if xs and ys:
                length_mm = max(xs) - min(xs)
                width_mm = max(ys) - min(ys)
    except Exception:
        pass

    params["footprint_sqm"] = round(
        length_mm * width_mm / 1e6, 1)
    params["total_area_sqm"] = round(
        params["footprint_sqm"] * max(1, params["total_floors"]), 1)

    return params


def check_fire(params, standards):
    u"""防火审查 (GB50016)。"""
    checks = []
    fire = standards.get("fire", {})

    height_mm = params.get("building_height_mm", 0)
    floors = params.get("total_floors", 0)

    max_h = fire.get("max_height_m", 100) * 1000
    if height_mm > max_h:
        checks.append({
            "item": u"建筑高度",
            "status": u"fail",
            "message": u"建筑高度 %.1fm 超过 %s 类建筑限值 %.1fm" % (
                height_mm / 1000.0, fire.get("building_type", u""),
                max_h / 1000.0),
            "standard": u"GB50016-2014",
            "suggestion": u"调整建筑高度或提升防火等级",
        })
    else:
        checks.append({
            "item": u"建筑高度",
            "status": u"pass",
            "message": u"建筑高度 %.1fm 在限值 %.1fm 以内" % (
                height_mm / 1000.0, max_h / 1000.0),
            "standard": u"GB50016-2014",
            "suggestion": u"",
        })

    max_floor_h = fire.get("max_floor_height_m", 6) * 1000
    for i, h in enumerate(params.get("floor_heights_mm", [])):
        if h > max_floor_h:
            checks.append({
                "item": u"第%d层层高" % (i + 1),
                "status": u"warning",
                "message": u"层高 %dmm 超过建议值 %dmm" % (h, max_floor_h),
                "standard": u"GB50016-2014",
                "suggestion": u"核实该层层高是否必要",
            })

    if not params.get("has_fire_rating"):
        checks.append({
            "item": u"构件耐火极限",
            "status": u"warning",
            "message": u"未检测到构件耐火极限参数",
            "standard": u"GB50016-2014",
            "suggestion": u"建议为墙/柱/楼板设置耐火极限参数",
        })

    return checks


def check_seismic(params, standards):
    u"""抗震审查 (GB50011)。"""
    checks = []
    seismic = standards.get("seismic", {})

    height_mm = params.get("building_height_mm", 0)
    max_h = seismic.get("max_height_m", 50) * 1000
    struct_type = seismic.get("applicable_struct_type", u"")

    if height_mm > max_h:
        checks.append({
            "item": u"抗震限高",
            "status": u"fail",
            "message": (u"建筑高度 %.1fm 超过 %s 结构抗震限高 %.1fm" % (
                height_mm / 1000.0, struct_type, max_h / 1000.0)),
            "standard": u"GB50011-2010",
            "suggestion": u"需进行超限审查或调整结构方案",
        })
    else:
        checks.append({
            "item": u"抗震限高",
            "status": u"pass",
            "message": u"建筑高度 %.1fm 在抗震限高 %.1fm 以内" % (
                height_mm / 1000.0, max_h / 1000.0),
            "standard": u"GB50011-2010",
            "suggestion": u"",
        })

    floors = params.get("total_floors", 0)
    if floors > 0:
        avg_h = sum(params.get("floor_heights_mm", [])) / max(1, len(
            params.get("floor_heights_mm", [])))
        if avg_h > 4000:
            checks.append({
                "item": u"层间刚度",
                "status": u"warning",
                "message": u"平均层高 %dmm 偏大，需验算层间刚度比" % round(avg_h),
                "standard": u"GB50011-2010",
                "suggestion": u"验算层间位移角是否满足 1/800 (框架) 或 1/1000 (剪力墙)",
            })

    return checks


def check_structure(params, standards):
    u"""结构审查。"""
    checks = []
    struct = standards.get("structure", {})

    wall_t = params.get("wall_thickness_mm", 0)
    min_t = struct.get("min_wall_thickness_mm", 160)
    if wall_t > 0 and wall_t < min_t:
        checks.append({
            "item": u"墙体厚度",
            "status": u"fail",
            "message": u"墙厚 %dmm 小于规范最小值 %dmm" % (wall_t, min_t),
            "standard": u"GB50010-2010",
            "suggestion": u"增加墙厚至 %dmm 以上" % min_t,
        })
    elif wall_t > 0:
        checks.append({
            "item": u"墙体厚度",
            "status": u"pass",
            "message": u"墙厚 %dmm 满足规范最小值 %dmm" % (wall_t, min_t),
            "standard": u"GB50010-2010",
            "suggestion": u"",
        })

    heights = params.get("floor_heights_mm", [])
    if heights:
        max_h = max(heights)
        slab_min = struct.get("min_slab_span_ratio", 1.0 / 30)
        checks.append({
            "item": u"楼板跨厚比",
            "status": u"info",
            "message": u"最大层高 %dmm，楼板厚度建议不小于跨度的 1/%d" % (
                max_h, int(1 / slab_min)),
            "standard": u"GB50010-2010",
            "suggestion": u"核实楼板厚度是否满足跨厚比要求",
        })

    return checks


def check_area(params, standards):
    u"""面积审查 (GBT50353)。"""
    checks = []
    area = standards.get("area", {})

    total = params.get("total_area_sqm", 0)
    footprint = params.get("footprint_sqm", 0)
    floors = params.get("total_floors", 0)

    if total > 0:
        checks.append({
            "item": u"建筑面积",
            "status": u"info",
            "message": u"估算总建筑面积: %.1f m2 (占地 %.1f m2 x %d 层)" % (
                total, footprint, max(1, floors)),
            "standard": u"GBT50353-2013",
            "suggestion": u"实际面积应按规范计算规则逐层核算（含阳台、飘窗等）",
        })

    return checks


def check_energy(params, standards):
    u"""节能审查。"""
    checks = []
    energy = standards.get("energy", {})

    wall_t = params.get("wall_thickness_mm", 0)
    min_insulation = energy.get("min_wall_insulation_mm", 0)
    if min_insulation > 0 and wall_t > 0:
        if wall_t < min_insulation:
            checks.append({
                "item": u"墙体保温",
                "status": u"warning",
                "message": u"墙厚 %dmm 可能不满足节能要求 (建议保温层 >= %dmm)" % (
                    wall_t, min_insulation),
                "standard": energy.get("standard_ref", u"地方节能标准"),
                "suggestion": u"核实外墙保温构造是否满足地方节能要求",
            })
        else:
            checks.append({
                "item": u"墙体保温",
                "status": u"info",
                "message": u"墙厚 %dmm，需进一步核算传热系数 U 值" % wall_t,
                "standard": energy.get("standard_ref", u"地方节能标准"),
                "suggestion": u"建议进行热工计算，确认 U 值满足限值",
            })

    return checks


def run_review(doc, location=u"", standards=None):
    u"""执行完整审查，返回审查报告 dict。"""
    if standards is None:
        standards = load_standalone_standards(location)

    params = extract_model_params(doc)
    if location:
        params["location"] = location

    all_checks = []
    all_checks.extend(check_fire(params, standards))
    all_checks.extend(check_seismic(params, standards))
    all_checks.extend(check_structure(params, standards))
    all_checks.extend(check_area(params, standards))
    all_checks.extend(check_energy(params, standards))

    fail_count = sum(1 for c in all_checks if c["status"] == u"fail")
    warn_count = sum(1 for c in all_checks if c["status"] == u"warning")
    score = max(0, min(100, 100 - fail_count * 20 - warn_count * 5))

    if score >= 90:
        grade = u"优秀"
    elif score >= 75:
        grade = u"良好"
    elif score >= 60:
        grade = u"及格"
    else:
        grade = u"需整改"

    feedback = []
    for c in all_checks:
        if c["status"] == u"fail":
            feedback.append({
                "priority": u"high",
                "action": c["suggestion"],
                "item": c["item"],
            })
        elif c["status"] == u"warning":
            feedback.append({
                "priority": u"medium",
                "action": c["suggestion"],
                "item": c["item"],
            })

    return {
        "score": score,
        "grade": grade,
        "checks": all_checks,
        "feedback": feedback,
        "model_params": params,
        "fail_count": fail_count,
        "warn_count": warn_count,
    }


def load_standalone_standards(location=u""):
    u"""加载独立规范数据（不依赖项目目录的完整数据库）。"""
    lib_dir = os.path.dirname(os.path.abspath(__file__))
    path = os.path.join(lib_dir, "standards_quick.json")
    if os.path.exists(path):
        with open(path, "rb") as f:
            raw = f.read()
        if raw[:3] == b"\xef\xbb\xbf":
            raw = raw[3:]
        return json.loads(raw.decode("utf-8"))

    return {
        "fire": {
            "max_height_m": 100,
            "max_floor_height_m": 6,
            "building_type": u"多层/高层",
        },
        "seismic": {
            "max_height_m": 50,
            "applicable_struct_type": u"框架",
            "drift_limit_frame": 1.0 / 550,
            "drift_limit_shear_wall": 1.0 / 1000,
        },
        "structure": {
            "min_wall_thickness_mm": 160,
            "min_slab_span_ratio": 1.0 / 30,
            "concrete_min_grade": u"C25",
        },
        "energy": {
            "min_wall_insulation_mm": 0,
            "max_u_value_wall": 0.6,
            "max_u_value_roof": 0.45,
            "standard_ref": u"GB50176",
        },
        "area": {
            "calculation_rules": u"GBT50353-2013",
        },
    }
