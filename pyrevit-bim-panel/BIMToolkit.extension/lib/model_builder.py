# -*- coding: utf-8 -*-
"""
model_builder.py — 建筑模型自动生成引擎

从 building_params.json 读取参数，自动创建：
  - 标高（按层高）
  - 外墙（按轮廓）
  - 内墙（按分隔墙定义）
  - 楼板（每层）
  - 坡屋顶（双坡/人字形）
  - 窗户（按墙面均匀分布）
  - 门（入口门 + 室内门）
  - 阳台（悬挑板）

技术要点：
  - Wall.Create 使用 8 参重载（唯一无歧义）
  - 墙类型按 WallKind.Basic 过滤（避开幕墙/叠层墙）
  - 坡屋顶使用 NewFootPrintRoof + set_DefinesSlope
  - 单位：mm / 304.8 = ft
"""
try:
    from Autodesk.Revit.DB import (
        Line, XYZ, Transaction, Level, Wall, WallType, WallKind,
        FilteredElementCollector, BuiltInCategory, SketchPlane, Plane,
        CurveArray, RoofType, ElementId, FloorType, ModelCurveArray,
        BuiltInParameter,
    )
except ImportError:
    from Autodesk.Revit.DB import (
        Line, XYZ, Transaction, Level, Wall, WallType, WallKind,
        FilteredElementCollector, BuiltInCategory, SketchPlane, Plane,
        CurveArray, RoofType, ElementId, FloorType, ModelCurveArray
    )
# NOTE: CompoundStructureLayerFunction 在 2019 不存在（2019 用
# MaterialFunctionAssignment）；结构层一律按名字 "Structure" 比较。
from Autodesk.Revit.DB.Structure import StructuralType

# StrongBox：IronPython 里出参（out 参数）必须显式传 StrongBox[T]。
#
# 2026-10-02 真机修：这里原本写的是
#     from System.Runtime.CompilerServices import StrongBox
# 在 Revit 2019 + pyRevit IronPython 2.7.12 上**直接 ImportError**
# （"Cannot import name StrongBox"），导致「一键建模」「一键演示」两个按钮
# 一点就炸。同一仓库的 bridge_core.py:1774 用的是 `from clr import StrongBox`，
# 那条路是通的 —— 以它为准，并保留旧写法作兜底。
#
# 为什么测试没抓到：harness 的宽松打桩会把任何 import 自动补上，
# 于是假绿灯。**这类"真机命名空间"问题只有真跑才看得见。**
try:
    from clr import StrongBox
except Exception:
    try:
        from System.Runtime.CompilerServices import StrongBox
    except Exception:
        # 两级都失败就必须**立刻炸**，而不是兜底成 None。
        # 兜底成 None 只会把错误推迟到使用处（StrongBox[T](...) 会报
        # "NoneType object is not subscriptable"），又是一个难查的错。
        # 本模块没有 StrongBox 根本无法工作（第 4 步坡屋顶要用它传出参）。
        raise ImportError(
            u"model_builder 需要 StrongBox，两种写法都导入失败：\n"
            u"  1) from clr import StrongBox                          (推荐)\n"
            u"  2) from System.Runtime.CompilerServices import StrongBox\n"
            u"正确写法见同一扩展的 bridge_core.py（NewFootPrintRoof 出参）。\n"
            u"若两条都不通，说明运行的 IronPython 环境不是 pyRevit 的 —— "
            u"检查是不是被 CPython 直接 import 了。")

from Autodesk.Revit.DB import (
    IFailuresPreprocessor, FailureProcessingResult, FailureSeverity,
)


class _SwallowWarnings(IFailuresPreprocessor):
    u"""提交事务时自动删除警告，避免 Revit 弹模态警告框阻塞自动化。

    T1'挂起'实测根因：commit 弹出 0 错误 N 警告 的模态框，
    ExternalEvent Execute 被永久阻塞，写队列全瘫。
    """

    def PreprocessFailures(self, fa):
        try:
            for msg in fa.GetFailureMessages():
                try:
                    if msg.GetSeverity() == FailureSeverity.Warning:
                        fa.DeleteWarning(msg)
                except Exception:
                    pass
        except Exception:
            pass
        return FailureProcessingResult.Continue


def _arm_tx(t):
    u"""给事务装警告吞噬器。"""
    try:
        fho = t.GetFailureHandlingOptions()
        fho.SetFailuresPreprocessor(_SwallowWarnings())
        t.SetFailureHandlingOptions(fho)
    except Exception:
        pass
    return t


def _activate(sym):
    u"""族符号未激活则激活（NewFamilyInstance 硬性要求）。"""
    try:
        if not sym.IsActive:
            sym.Activate()
        return True
    except Exception:
        return False


def _pick_active(types):
    u"""挑一个能激活的族符号；都失败返回 None。"""
    for s in types:
        if _activate(s):
            return s
    return None


FT_PER_MM = 1.0 / 304.8


def m(mm_val):
    """mm → ft"""
    return mm_val * FT_PER_MM


def to_element_list(collector):
    try:
        return list(collector.ToElements())
    except Exception:
        return list(collector)


def find_basic_wall_type(doc):
    for wt in to_element_list(FilteredElementCollector(doc).OfClass(WallType)):
        try:
            if wt.Kind == WallKind.Basic:
                return wt
        except Exception:
            pass
    return None


def set_wall_thickness(wall_type, thickness_mm):
    u"""把墙类型总厚改为 thickness_mm（改结构层，取不到再退第 0 层）。

    返回 True=成功；False=失败（调用方必须检查并提示，不得假成功）。
    """
    cs = wall_type.GetCompoundStructure()
    if cs is None or cs.LayerCount <= 0:
        return False
    try:
        # 优先改结构层。2019 枚举是 MaterialFunctionAssignment、
        # 2020+ 是 CompoundStructureLayerFunction——按名字比较跨版本通用。
        idx = 0
        try:
            for i in range(cs.LayerCount):
                if to_text_safe(cs.GetLayerFunction(i)) == "Structure":
                    idx = i
                    break
        except Exception:
            pass
        cs.SetLayerWidth(idx, m(thickness_mm))
        wall_type.SetCompoundStructure(cs)
        return True
    except Exception:
        return False


def ensure_wall_type(doc, base_type, name, thickness_mm, warn):
    u"""按名字复用已存在的墙类型；不存在才 Duplicate 并改厚。

    返回 (wall_type, ok)。Duplicate 固定名二次运行会抛"名称已存在"，
    本函数先查已有同名类型复用，避免墙厚静默丢失。
    """
    for wt in to_element_list(FilteredElementCollector(doc).OfClass(WallType)):
        try:
            if to_text_safe(_type_name(wt)) == name:
                return wt, set_wall_thickness(wt, thickness_mm)
        except Exception:
            pass
    try:
        new_wt = base_type.Duplicate(name)
        ok = set_wall_thickness(new_wt, thickness_mm)
        if not ok:
            warn(u"   警告: 墙类型 %s 改厚失败，将按默认厚度创建" % name)
        return new_wt, ok
    except Exception:
        warn(u"   警告: 创建墙类型 %s 失败，回退基础墙类型" % name)
        return base_type, False


def to_text_safe(v):
    try:
        return unicode(v)
    except NameError:
        return str(v)
    except Exception:
        try:
            return unicode(v, "utf-8", "replace")
        except Exception:
            return u"<无法转换>"


def _type_name(et):
    u"""读取类型名（IPY 对 ElementType new 隐藏的 Name 属性会
    AttributeError: Name——实测 WallType.Name 炸、Level.Name 正常）。
    回退内置参数 ALL_MODEL_TYPE_NAME。"""
    try:
        return et.Name
    except Exception:
        pass
    try:
        p = et.get_Parameter(BuiltInParameter.ALL_MODEL_TYPE_NAME)
        if p is not None:
            return p.AsString()
    except Exception:
        pass
    return u""


def create_levels(doc, params):
    binfo = params.get("building_info", {})
    total_floors = binfo.get("total_floors", 1)
    floor_height_mm = binfo.get("floor_height_mm", 3000)

    existing = to_element_list(FilteredElementCollector(doc).OfClass(Level))
    levels = []

    if existing:
        base = existing[0]
        levels.append(base)
    else:
        t = Transaction(doc, u"AI-创建标高")
        t.Start()
        _arm_tx(t)
        lv = Level.Create(doc, 0.0)
        try:
            lv.Name = u"AI-1F"
        except Exception:
            pass
        levels.append(lv)
        t.Commit()

    for i in range(1, total_floors + 1):
        cum_mm = floor_height_mm * i
        elev_ft = m(cum_mm)
        t = Transaction(doc, u"AI-创建标高L%d" % i)
        t.Start()
        _arm_tx(t)
        lv = Level.Create(doc, elev_ft)
        name = u"AI-%dF" % (i + 1)
        try:
            lv.Name = name
        except Exception:
            pass
        levels.append(lv)
        t.Commit()

    return levels


def create_exterior_walls(doc, params, levels, warn=None):
    u"""创建外墙。返回 (墙列表, 失败数)。事务提交状态逐个核对。"""
    def _w(msg):
        if warn:
            warn(msg)

    binfo = params.get("building_info", {})
    total_floors = binfo.get("total_floors", 1)
    length_mm = binfo.get("footprint_length_m", 12.0) * 1000
    width_mm = binfo.get("footprint_width_m", 9.0) * 1000
    floor_height_mm = binfo.get("floor_height_mm", 3000)

    structural = params.get("structural_system", {})
    wall_t_mm = structural.get("wall_thickness_mm", 200)

    wall_type = find_basic_wall_type(doc)
    if wall_type is None:
        _w(u"   错误: 项目中未找到基本墙(Basic)类型，外墙未创建")
        return [], 0

    all_walls = []
    fail = 0
    wall_h_ft = m(floor_height_mm + 100)

    for fi in range(min(total_floors, len(levels))):
        base_elev = levels[fi].Elevation
        p0 = XYZ(0, 0, base_elev)
        p1 = XYZ(m(length_mm), 0, base_elev)
        p2 = XYZ(m(length_mm), m(width_mm), base_elev)
        p3 = XYZ(0, m(width_mm), base_elev)
        wall_lines = [(p0, p1), (p1, p2), (p2, p3), (p3, p0)]

        t = Transaction(doc, u"AI-建墙-第%d层" % (fi + 1))
        t.Start()
        _arm_tx(t)

        my_wt, _ok = ensure_wall_type(
            doc, wall_type,
            u"AI-W%d-%dmm" % (fi + 1, wall_t_mm), wall_t_mm, _w)

        made = 0
        for pa, pb in wall_lines:
            line = Line.CreateBound(pa, pb)
            try:
                w = Wall.Create(doc, line, my_wt.Id, levels[fi].Id,
                                wall_h_ft, 0.0, False, False)
                all_walls.append(w)
                made += 1
            except Exception as e:
                fail += 1
                _w(u"   墙创建失败(第%d层): %s" % (fi + 1, to_text_safe(e)))

        status = t.Commit()
        try:
            if str(status) != "Committed":
                fail += (len(wall_lines) - made)
                all_walls = [w for w in all_walls if _alive(w)]
                _w(u"   第%d层墙事务提交失败(状态=%s)，本层已回滚" % (
                    fi + 1, to_text_safe(status)))
        except Exception:
            pass

    return all_walls, fail


def _alive(el):
    try:
        return el is not None and el.IsValidObject
    except Exception:
        return False


def create_interior_walls(doc, params, levels, warn=None):
    u"""创建内墙。返回 (墙列表, 失败数)。"""
    def _w(msg):
        if warn:
            warn(msg)

    binfo = params.get("building_info", {})
    total_floors = binfo.get("total_floors", 1)
    floor_height_mm = binfo.get("floor_height_mm", 3000)
    interior_walls = binfo.get("interior_walls", [])

    if not interior_walls:
        return [], 0

    wall_type = find_basic_wall_type(doc)
    if wall_type is None:
        _w(u"   错误: 项目中未找到基本墙(Basic)类型，内墙未创建")
        return [], 0

    all_int_walls = []
    fail = 0
    wall_h_ft = m(floor_height_mm + 100)

    for fi in range(min(total_floors, len(levels))):
        base_elev = levels[fi].Elevation

        t = Transaction(doc, u"AI-内墙-第%d层" % (fi + 1))
        t.Start()
        _arm_tx(t)

        for idx, iw in enumerate(interior_walls):
            x1_m = iw.get("start_x_m", 0)
            y1_m = iw.get("start_y_m", 0)
            x2_m = iw.get("end_x_m", 0)
            y2_m = iw.get("end_y_m", 0)
            thickness = iw.get("thickness_mm", 120)

            my_wt, _ok = ensure_wall_type(
                doc, wall_type,
                u"AI-IW%d-%d-%dmm" % (fi + 1, idx + 1, thickness),
                thickness, _w)

            p1 = XYZ(m(x1_m * 1000), m(y1_m * 1000), base_elev)
            p2 = XYZ(m(x2_m * 1000), m(y2_m * 1000), base_elev)
            line = Line.CreateBound(p1, p2)

            try:
                w = Wall.Create(doc, line, my_wt.Id, levels[fi].Id,
                                wall_h_ft, 0.0, False, False)
                all_int_walls.append(w)
            except Exception as e:
                fail += 1
                _w(u"   内墙创建失败(第%d层第%d道): %s" % (
                    fi + 1, idx + 1, to_text_safe(e)))

        status = t.Commit()
        try:
            if str(status) != "Committed":
                fail += len(interior_walls)
                all_int_walls = [w for w in all_int_walls if _alive(w)]
                _w(u"   第%d层内墙事务提交失败(状态=%s)" % (
                    fi + 1, to_text_safe(status)))
        except Exception:
            pass

    return all_int_walls, fail


def create_floors(doc, params, levels):
    binfo = params.get("building_info", {})
    total_floors = binfo.get("total_floors", 1)
    length_mm = binfo.get("footprint_length_m", 12.0) * 1000
    width_mm = binfo.get("footprint_width_m", 9.0) * 1000

    floor_types = to_element_list(
        FilteredElementCollector(doc).OfClass(FloorType))
    if not floor_types:
        return 0

    count = 0
    for fi in range(min(total_floors + 1, len(levels))):
        elev = levels[fi].Elevation
        t = Transaction(doc, u"AI-楼板-第%d层" % (fi + 1))
        t.Start()
        _arm_tx(t)

        ca = CurveArray()
        ca.Append(Line.CreateBound(
            XYZ(0, 0, elev), XYZ(m(length_mm), 0, elev)))
        ca.Append(Line.CreateBound(
            XYZ(m(length_mm), 0, elev), XYZ(m(length_mm), m(width_mm), elev)))
        ca.Append(Line.CreateBound(
            XYZ(m(length_mm), m(width_mm), elev), XYZ(0, m(width_mm), elev)))
        ca.Append(Line.CreateBound(
            XYZ(0, m(width_mm), elev), XYZ(0, 0, elev)))

        sp = SketchPlane.Create(doc, Plane.CreateByNormalAndOrigin(
            XYZ(0, 0, 1), XYZ(0, 0, elev)))

        try:
            doc.Create.NewFloor(ca, floor_types[0], levels[fi], True)
            count += 1
        except Exception:
            pass
        t.Commit()

    return count


def create_gable_roof(doc, params, levels, all_walls):
    binfo = params.get("building_info", {})
    roof_info = params.get("roof", {})
    total_floors = binfo.get("total_floors", 1)
    length_mm = binfo.get("footprint_length_m", 12.0) * 1000
    width_mm = binfo.get("footprint_width_m", 9.0) * 1000

    if len(levels) <= total_floors:
        return None

    top_elev = levels[total_floors].Elevation
    slope_angle = roof_info.get("slope_angle_deg", 30) * 0.0174533

    roof_types = to_element_list(FilteredElementCollector(doc).OfClass(RoofType))
    if not roof_types:
        return None

    overhang = roof_info.get("overhang_mm", 300)
    x0 = m(-overhang)
    y0 = m(-overhang)
    x1 = m(length_mm + overhang)
    y1 = m(width_mm + overhang)

    t = Transaction(doc, u"AI-坡屋顶")
    t.Start()
    _arm_tx(t)

    ca = CurveArray()
    ca.Append(Line.CreateBound(XYZ(x0, y0, top_elev), XYZ(x1, y0, top_elev)))
    ca.Append(Line.CreateBound(XYZ(x1, y0, top_elev), XYZ(x1, y1, top_elev)))
    ca.Append(Line.CreateBound(XYZ(x1, y1, top_elev), XYZ(x0, y1, top_elev)))
    ca.Append(Line.CreateBound(XYZ(x0, y1, top_elev), XYZ(x0, y0, top_elev)))

    sp = SketchPlane.Create(doc, Plane.CreateByNormalAndOrigin(
        XYZ(0, 0, 1), XYZ(0, 0, top_elev)))

    roof_elem = None
    model_curve_array = None
    try:
        # IPY2.7 不做 out 参数元组返回，必须显式传 StrongBox[T]
        # （实测报 TypeError: expected StrongBox[ModelCurveArray]）
        mca_box = StrongBox[ModelCurveArray](ModelCurveArray())  # 必须带初值，否则 Value cannot be null
        roof_elem = doc.Create.NewFootPrintRoof(
            ca, levels[total_floors], roof_types[0], mca_box)
        model_curve_array = mca_box.Value
    except Exception:
        t.Commit()
        return None

    if roof_elem and model_curve_array:
        try:
            for i in range(model_curve_array.Size):
                mc = model_curve_array[i]
                curve = mc.GeometryCurve
                if curve:
                    mid_pt = curve.Evaluate(0.5, True)
                    is_gable_end = (abs(mid_pt.X - x0) < 0.1 or
                                    abs(mid_pt.X - x1) < 0.1)
                    if not is_gable_end:
                        roof_elem.set_DefinesSlope(mc, True)
                        roof_elem.set_SlopeAngle(mc, slope_angle)
                    else:
                        roof_elem.set_DefinesSlope(mc, False)
        except Exception:
            pass

    # 2019 无公开的墙-屋顶附着 API（Wall.AttachWallTop 不存在，勿再尝试）。
    # 墙高已在创建时按层高+100mm 给定；如需精确贴合屋脊，后续可设墙的
    # 顶部约束参数(WALL_TOP_OFFSET)或手动"附着顶部/底部"。
    attached = 0

    t.Commit()
    return roof_elem


def create_windows(doc, params, levels, all_walls, warn=None):
    u"""创建窗户。返回成功数（失败逐个提示，不再一败全弃）。"""
    def _w_win(msg):
        if warn:
            warn(msg)

    openings = params.get("openings", {})
    win = openings.get("windows", {})
    win_w_mm = win.get("width_mm", 1500)
    win_h_mm = win.get("height_mm", 1500)
    sill_h_mm = win.get("sill_height_mm", 900)
    count_per_wall = win.get("count_per_wall", [2, 2, 1, 1])

    win_types = to_element_list(
        FilteredElementCollector(doc).OfCategory(BuiltInCategory.OST_Windows)
        .WhereElementIsElementType())
    if not win_types or not all_walls:
        return 0

    binfo = params.get("building_info", {})
    total_floors = binfo.get("total_floors", 1)

    count = 0
    fail = 0
    t = Transaction(doc, u"AI-窗户")
    t.Start()
    _arm_tx(t)
    win_sym = _pick_active(win_types)
    if win_sym is None:
        t.RollBack()
        return 0

    for fi in range(min(total_floors, len(levels))):
        base_elev = levels[fi].Elevation
        win_center_z = base_elev + m(sill_h_mm) + m(win_h_mm) / 2.0

        for wi in range(4):
            wall_idx = fi * 4 + wi
            if wall_idx >= len(all_walls):
                continue
            wall = all_walls[wall_idx]
            n_wins = count_per_wall[wi] if wi < len(count_per_wall) else 2

            loc = wall.Location
            if not hasattr(loc, 'Curve'):
                continue
            curve = loc.Curve
            for j in range(n_wins):
                t_param = (j + 1.0) / (n_wins + 1.0)
                mid = curve.Evaluate(t_param, True)
                pt = XYZ(mid.X, mid.Y, win_center_z)
                try:
                    doc.Create.NewFamilyInstance(
                        pt, win_sym, wall, StructuralType.NonStructural)
                    count += 1
                except Exception as e:
                    fail += 1
                    if fail <= 3:
                        _w_win(u"   窗创建失败: %s" % to_text_safe(e))

    status = t.Commit()
    try:
        if str(status) != "Committed":
            _w_win(u"   窗事务提交失败(状态=%s)，窗户未落盘" % to_text_safe(status))
            return 0
    except Exception:
        pass
    return count


def create_doors(doc, params, levels, all_walls, warn=None):
    u"""创建入口门。返回成功数。"""
    def _w_dr(msg):
        if warn:
            warn(msg)

    openings = params.get("openings", {})
    door = openings.get("doors", {}).get("entry", {})
    door_h_mm = door.get("height_mm", 2100)

    door_types = to_element_list(
        FilteredElementCollector(doc).OfCategory(BuiltInCategory.OST_Doors)
        .WhereElementIsElementType())
    if not door_types or not all_walls:
        return 0

    count = 0
    t = Transaction(doc, u"AI-门")
    t.Start()
    _arm_tx(t)
    door_sym = _pick_active(door_types)
    if door_sym is None:
        t.RollBack()
        return 0

    front_wall = all_walls[0]
    base_elev = levels[0].Elevation
    door_center_z = base_elev + m(door_h_mm) / 2.0

    loc = front_wall.Location
    if hasattr(loc, 'Curve'):
        curve = loc.Curve
        mid = curve.Evaluate(0.5, True)
        pt = XYZ(mid.X, mid.Y, door_center_z)
        try:
            doc.Create.NewFamilyInstance(
                pt, door_sym, front_wall, StructuralType.NonStructural)
            count += 1
        except Exception as e:
            _w_dr(u"   门创建失败: %s" % to_text_safe(e))

    status = t.Commit()
    try:
        if str(status) != "Committed":
            _w_dr(u"   门事务提交失败(状态=%s)" % to_text_safe(status))
            return 0
    except Exception:
        pass

    return count


def create_balconies(doc, params, levels):
    binfo = params.get("building_info", {})
    balcony_info = binfo.get("balcony", {})
    if not balcony_info.get("enabled", False):
        return 0

    total_floors = binfo.get("total_floors", 1)
    length_mm = binfo.get("footprint_length_m", 12.0) * 1000

    floor_types = to_element_list(FilteredElementCollector(doc).OfClass(FloorType))
    if not floor_types:
        return 0

    balcony_width_mm = balcony_info.get("width_mm", 1500)
    balcony_length_mm = balcony_info.get("length_mm", 4000)

    count = 0
    for fi in range(1, min(total_floors, len(levels))):
        base_elev = levels[fi].Elevation
        x_center = m(length_mm) / 2.0
        x0 = x_center - m(balcony_length_mm) / 2.0
        x1 = x_center + m(balcony_length_mm) / 2.0
        y0 = m(-balcony_width_mm)
        y1 = 0

        t = Transaction(doc, u"AI-阳台-第%d层" % (fi + 1))
        t.Start()
        _arm_tx(t)

        ca = CurveArray()
        ca.Append(Line.CreateBound(XYZ(x0, y0, base_elev), XYZ(x1, y0, base_elev)))
        ca.Append(Line.CreateBound(XYZ(x1, y0, base_elev), XYZ(x1, y1, base_elev)))
        ca.Append(Line.CreateBound(XYZ(x1, y1, base_elev), XYZ(x0, y1, base_elev)))
        ca.Append(Line.CreateBound(XYZ(x0, y1, base_elev), XYZ(x0, y0, base_elev)))

        sp = SketchPlane.Create(doc, Plane.CreateByNormalAndOrigin(
            XYZ(0, 0, 1), XYZ(0, 0, base_elev)))

        try:
            doc.Create.NewFloor(ca, floor_types[0], levels[fi], True)
            count += 1
        except Exception:
            pass

        t.Commit()

    return count


def build_model(doc, params, output_func=None):
    """主入口：按参数生成完整模型"""
    def _print(msg):
        if output_func:
            output_func(msg)

    binfo = params.get("building_info", {})
    total_floors = binfo.get("total_floors", 1)
    length = binfo.get("footprint_length_m", 12.0)
    width = binfo.get("footprint_width_m", 9.0)

    _print(u"### 1/7 创建标高...")
    levels = create_levels(doc, params)
    _print(u"   标高: %d 个" % len(levels))

    _print(u"### 2/7 创建外墙...")
    all_walls, ext_fail = create_exterior_walls(doc, params, levels, _print)
    _print(u"   外墙: %d 堵%s" % (len(all_walls),
        (u"（失败 %d，见上方原因）" % ext_fail) if ext_fail else u""))

    _print(u"### 3/7 创建内墙...")
    int_walls, int_fail = create_interior_walls(doc, params, levels, _print)
    _print(u"   内墙: %d 堵%s" % (len(int_walls),
        (u"（失败 %d，见上方原因）" % int_fail) if int_fail else u""))

    _print(u"### 4/7 创建楼板...")
    floor_count = create_floors(doc, params, levels)
    _print(u"   楼板: %d 块" % floor_count)

    _print(u"### 5/7 创建坡屋顶...")
    roof_elem = create_gable_roof(doc, params, levels, all_walls + int_walls)
    _print(u"   屋顶: %s" % (u"已创建" if roof_elem else u"未创建"))
    _print(u"   注: 墙顶自动附着屋面 2019 API 不支持，墙高按层高+100mm 预留")

    _print(u"### 6/7 创建门窗...")
    win_count = create_windows(doc, params, levels, all_walls, _print)
    door_count = create_doors(doc, params, levels, all_walls, _print)
    _print(u"   窗户: %d | 门: %d" % (win_count, door_count))

    _print(u"### 7/7 创建阳台...")
    balcony_count = create_balconies(doc, params, levels)
    _print(u"   阳台: %d 个" % balcony_count)

    return {
        "levels": len(levels),
        "ext_walls": len(all_walls),
        "int_walls": len(int_walls),
        "floors": floor_count,
        "roof": roof_elem is not None,
        "windows": win_count,
        "doors": door_count,
        "balconies": balcony_count,
        "fail_count": ext_fail + int_fail,
    }
