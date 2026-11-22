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
除法一律真除（`from __future__ import division`）—— 本模块要算层高平均值、
跨厚比这类比值，整数除法会把 8000/3 变成 2666，也会让"密度型"判据静默归零
（bimhealth 已因此踩过一次，真 IPY2.7 实测 50/770*100.0 -> 0.0）。
"""
from __future__ import division
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

# 名称里带这些词的标高不是"层"，是屋顶/雨篷这类构件的锚点。
# 只作为**排除**线索使用，不反过来当作楼层依据；判定依据见 n_storeys_with_content。
_NON_STOREY_MARKS = (u"屋顶", u"屋面", u"雨篷", u"雨棚", u"吊顶", u"坡道",
                     "ROOF", "Roof", "CANOPY", "Ceiling", "RAMP")


def mm(ft_val):
    return ft_val / FT_PER_MM


def _level_host_count(doc, level_id):
    u"""该标高上挂着的实例构件数。返回 -1 表示取不到（区分 0 与"不知道"）。"""
    try:
        col = DB.FilteredElementCollector(doc) \
            .WherePasses(DB.ElementLevelFilter(level_id)) \
            .WherePasses(DB.ElementIsElementTypeFilter(True))
        return len(to_element_list(col))
    except Exception:
        return -1


def _is_storey_name(name):
    for mark in _NON_STOREY_MARKS:
        if mark and mark in name:
            return False
    return True


def extract_model_params(doc):
    u"""从当前模型提取关键参数。

    2026-10-07 真机核对后重写的三条原则：
      1. **不再用 len(levels)-1 当层数** —— 屋顶/雨篷标高也被算了进去。
         改为同时给出 n_levels / n_storeys，并说明依据，让人能否决。
      2. **取不到的值写 None 并登记到 unavailable**，不再用 0 / "" 冒充
         "算出来是 0"。0 和"没数据"在审查里是两件完全不同的事。
      3. 层高只在**楼层之间**算，退化值（<300mm）丢掉，避免把 0 和 500
         这种锚点差当成层高喂给后续判定。
    """
    params = {
        "n_levels": 0,
        "total_floors": 0,
        "floor_basis": u"",
        "floor_heights_mm": [],
        "building_height_mm": 0,
        "wall_thickness_mm": 0,
        "wall_types_seen": 0,
        "structural_type": None,
        "has_fire_rating": False,
        "window_area_ratio": None,
        "total_area_sqm": None,
        "footprint_sqm": None,
        "location": u"",
    }
    unavailable = []

    levels = to_element_list(
        DB.FilteredElementCollector(doc).OfClass(DB.Level))
    levels = sorted(levels, key=lambda l: l.Elevation)
    params["n_levels"] = len(levels)

    storeys = []
    for lv in levels:
        name = to_text(lv.Name)
        if not _is_storey_name(name):
            continue
        n = _level_host_count(doc, lv.Id)
        if n == 0:
            continue          # 有标高但上面没有任何构件：不算已建成的层
        storeys.append(lv)
    params["total_floors"] = len(storeys)
    params["floor_basis"] = (
        u"%d 个标高，其中 %d 个按「名称非屋顶类 + 上面有构件」算作楼层"
        % (len(levels), len(storeys)))

    heights = []
    for i in range(1, len(storeys)):
        h_mm = mm(storeys[i].Elevation - storeys[i - 1].Elevation)
        if h_mm >= 300.0:
            heights.append(round(h_mm))
    params["floor_heights_mm"] = heights
    if len(storeys) < 2:
        unavailable.append(u"floor_heights_mm（楼层不足 2 个，算不出层高）")

    if levels:
        params["building_height_mm"] = round(
            mm(levels[-1].Elevation - levels[0].Elevation))

    walls = to_element_list(
        DB.FilteredElementCollector(doc).OfClass(DB.Wall)
        .WhereElementIsNotElementType())
    thicknesses = []
    if walls:
        seen = set()
        for w in walls:
            try:
                wt = doc.GetElement(w.WallType.Id)
                if wt is None:
                    continue
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
                if total_mm > 0:
                    thicknesses.append(total_mm)
                    seen.add(to_text(wt.Name))
            except Exception:
                continue
        if thicknesses:
            # 取最厚者作为审查对象：限值都是"不得小于"，取最薄会把合格判成不合格
            params["wall_thickness_mm"] = max(thicknesses)
            params["wall_types_seen"] = len(seen)
        else:
            unavailable.append(u"wall_thickness_mm（墙类型宽度都取不到）")
    else:
        unavailable.append(u"wall_thickness_mm（模型里没有墙）")

    for w in walls:
        try:
            p = w.get_Parameter(DB.BuiltInParameter.FIRE_RATING_PARAM)
            if p and p.HasValue:
                v = p.AsString()
                if v and v.strip():
                    params["has_fire_rating"] = True
                    break
        except Exception:
            pass

    length_mm = 0.0
    width_mm = 0.0
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

    if length_mm > 0 and width_mm > 0:
        params["footprint_sqm"] = round(length_mm * width_mm / 1e6, 1)
        params["total_area_sqm"] = round(
            params["footprint_sqm"] * max(1, params["total_floors"]), 1)
        params["footprint_basis"] = u"前 20 堵墙外包尺寸估算，非按规范计算规则"
    else:
        unavailable.append(u"footprint_sqm / total_area_sqm（取不到墙定位线）")

    unavailable.append(u"structural_type（模型里没有可读的结构体系字段）")
    unavailable.append(u"window_area_ratio（未实现窗面积统计）")
    params["unavailable"] = unavailable
    return params


def _not_checked(item, standard, reason):
    u"""三态里的第三态：**没检查**，而不是"检查了且通过"。

    此前这些情况的表现是"少一条记录" —— 记录少了，扣分自然也没了，
    于是"整个节能类别一条都没跑"能算出 95 分"优秀"。
    """
    return {
        "item": item,
        "status": u"not_checked",
        "message": u"未检查：%s" % reason,
        "standard": standard,
        "suggestion": u"",
    }


def check_fire(params, standards):
    u"""防火审查 (GB50016)。"""
    checks = []
    fire = standards.get("fire", {})

    height_mm = params.get("building_height_mm", 0)
    floors = params.get("total_floors", 0)

    max_h = fire.get("max_height_m", 100) * 1000
    if height_mm <= 0:
        checks.append(_not_checked(
            u"建筑高度", u"GB50016-2014",
            u"模型里取不到标高，算不出建筑高度"))
    elif height_mm > max_h:
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

    if height_mm <= 0:
        checks.append(_not_checked(
            u"抗震限高", u"GB50011-2010", u"取不到建筑高度"))
    elif height_mm > max_h:
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
    if wall_t <= 0:
        checks.append(_not_checked(
            u"墙体厚度", u"GB50010-2010",
            u"模型里没有墙，或墙类型宽度取不到"))
    elif wall_t < min_t:
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

    if not total:
        checks.append(_not_checked(
            u"建筑面积", u"GBT50353-2013",
            u"取不到墙定位线，算不出外包面积"))
    else:
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
    if not min_insulation:
        checks.append(_not_checked(
            u"墙体保温", energy.get("standard_ref", u"地方节能标准"),
            u"标准库里 min_wall_insulation_mm 是 %r，等于没有阈值 —— "
            u"这一类从未真正检查过，不要当成通过" % (min_insulation,)))
    elif wall_t <= 0:
        checks.append(_not_checked(
            u"墙体保温", energy.get("standard_ref", u"地方节能标准"),
            u"取不到墙厚"))
    elif wall_t < min_insulation:
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

    # 计分只覆盖**真正完成判定**的项。not_checked 不参与扣分，但必须显式出现在
    # 报告里并压低可信度 —— 否则"少一条记录"就等于"少一处问题"，
    # 整个节能类别没跑也能拿 95 分。
    judged = [c for c in all_checks
              if c["status"] in (u"pass", u"warning", u"fail")]
    not_checked = [c for c in all_checks if c["status"] == u"not_checked"]
    fail_count = sum(1 for c in judged if c["status"] == u"fail")
    warn_count = sum(1 for c in judged if c["status"] == u"warning")

    if not judged:
        score = None
        grade = u"无法评级：没有任何一项完成判定"
    else:
        score = max(0, min(100, 100 - fail_count * 20 - warn_count * 5))
        if score >= 90:
            grade = u"优秀"
        elif score >= 75:
            grade = u"良好"
        elif score >= 60:
            grade = u"及格"
        else:
            grade = u"需整改"
        if not_checked:
            grade = u"%s（仅 %d/%d 项完成判定）" % (
                grade, len(judged), len(judged) + len(not_checked))

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
        "judged_count": len(judged),
        "not_checked": not_checked,
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
