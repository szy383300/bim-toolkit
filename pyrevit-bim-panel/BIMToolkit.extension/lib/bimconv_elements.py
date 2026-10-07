# -*- coding: utf-8 -*-
u"""
bimconv_elements.py —— 翻模「构件创建」层：每种构件类型一个 _create_xxx()。

约定：
  - 每个 _create_xxx(doc, plan, ...) 只负责一种构件，失败就 raise（由上层逐构件
    try/except 捕获，记为「跳过」并在结果表里给出中文原因，绝不中断整体）。
  - 任何坐标必须经 bimconv_geom._xyz() 校验后再传给 Revit API。
  - 退化/非有限几何一律跳过 —— 这是防 Revit 原生崩溃的最后一道闸。

依赖：bimconv_geom（坐标）、bimconv_walls（门/窗寄宿时找最近墙）。
"""

try:
    from pyrevit import DB
except Exception:
    DB = None
# MCP bridge / embedded context: pyrevit import may fail silently -> DB=None.
# Fall back to the direct RevitAPI reference (same fix as patch_lib_db.py).
if DB is None:
    try:
        import clr
        clr.AddReference("RevitAPI")
        from Autodesk.Revit import DB
    except Exception:
        DB = None

from bimconv_geom import (MM_TO_FT, MIN_WALL_SEGMENT_LEN, MIN_WALL_TOTAL_LEN,
                          _num_ok, _xyz, _point_of)
from bimconv_walls import _nearest_wall
try:
    from bimlib import type_name
except Exception:
    def type_name(et):
        try:
            return et.Name
        except Exception:
            return u""


# --------------------------------------------------------------------------- #
# 类型/族查找辅助
# --------------------------------------------------------------------------- #
def _level_id(doc, name, _level_cache=None):
    u"""按名字找楼层 ElementId；找不到时退化为第一个楼层；没有楼层返回 None。

    _level_cache: {name: Id} 预取缓存，避免逐构件全库扫描。
    """
    if _level_cache is not None:
        lid = _level_cache.get(name)
        if lid is not None:
            return lid
        if _level_cache:
            first = next(iter(_level_cache.values()))
            return first
        return None
    levels = list(DB.FilteredElementCollector(doc).OfClass(DB.Level).ToElements())
    for lv in levels:
        if lv.Name == name:
            return lv.Id
    return levels[0].Id if levels else None


def _wall_type(doc, hint):
    u"""找墙类型（hint 模糊匹配名字，无 hint 取第一个）。"""
    types = list(DB.FilteredElementCollector(doc).OfClass(DB.WallType).ToElements())
    if not types:
        return None
    if hint:
        for t in types:
            if hint.lower() in type_name(t).lower():
                return t
    return types[0]


def _cat_name(s):
    u"""类别名安全读取（Category 可能为 None，别让过滤器炸掉整个列表推导）。"""
    try:
        return (s.Category.Name or u"") if s.Category else u""
    except Exception:
        return u""


def _column_symbol(doc, hint):
    u"""找柱族类型（按类别名含「柱/column」筛选，退化时取任意 FamilySymbol 首枚）。"""
    syms = [s for s in DB.FilteredElementCollector(doc).OfClass(DB.FamilySymbol).ToElements()
            if u"\u67f1" in _cat_name(s) or "column" in _cat_name(s).lower()]
    if not syms:
        # 退化：取任意 FamilySymbol 首枚（仍可能不对，靠预览提示）
        syms = list(DB.FilteredElementCollector(doc).OfClass(DB.FamilySymbol).ToElements())
    if not syms:
        return None
    if hint:
        for s in syms:
            if hint.lower() in type_name(s).lower():
                return s
    return syms[0]


def _floor_type(doc, hint):
    u"""找楼板类型（hint 模糊匹配名字，无 hint 取第一个）。"""
    types = list(DB.FilteredElementCollector(doc).OfClass(DB.FloorType).ToElements())
    if not types:
        return None
    if hint:
        for t in types:
            if hint.lower() in type_name(t).lower():
                return t
    return types[0]


# --------------------------------------------------------------------------- #
# 轴网 / 墙 / 柱 / 板
# --------------------------------------------------------------------------- #
def _create_grid(doc, plan):
    u"""轴网：由 start/end 两点建一条轴网。"""
    e = plan["geom"]
    s_raw = e.get("start")
    en_raw = e.get("end")
    if not (s_raw and en_raw):
        raise Exception(u"轴网缺少起点/终点，已跳过")
    s = _xyz(s_raw, 0)
    en = _xyz(en_raw, 0)
    d = s.DistanceTo(en)
    # NaN/inf 一律按退化跳过（'d < 阈值' 对 NaN 返回 False 会漏网 → 喂入 Revit 原生崩）
    if d != d or d >= float('inf') or d < MIN_WALL_SEGMENT_LEN:
        raise Exception(u"轴网退化或坐标非有限（长度 < %g mm 或越界），已跳过"
                        % (MIN_WALL_SEGMENT_LEN / MM_TO_FT))
    curve = DB.Line.CreateBound(s, en)
    return DB.Grid.Create(doc, curve)


def _create_wall(doc, plan, level_id, wt, collect=None, curves=None):
    u"""墙：多段线逐段建墙；单条 LINE（天正常见）按一段直墙处理。

    返回首段墙对象（整条墙线可能拆成多段，全部已建）。
    collect/curves 用于收集墙对象与其曲线，供后续 Join 和门/窗寄宿。
    """
    e = plan["geom"]
    pts = e.get("points") or []
    if len(pts) < 2 and e.get("start") and e.get("end"):
        # 天正图的墙常以单条 LINE 线段表示（只有 start/end），按一段直墙处理
        pts = [e["start"], e["end"]]
    if len(pts) < 2:
        raise Exception(u"墙至少需要 2 个点（当前点数不足），已跳过")
    elev = 0.0
    created = []
    seq = pts + [pts[0]] if e.get("closed") else pts
    # 退化段（零长/近零/非有限）必须跳过：DB.Wall.Create 喂入退化或 NaN 曲线会触发
    # Revit 原生崩溃（不可被 try/except 捕获，表现为「Revit 遇到了意外错误」闪退）。
    # 先把所有点转成有限坐标（_xyz 遇坏点直接抛，由上层记为跳过），
    # 再校验总长与逐段长度，NaN/inf 一律按「不合格」跳过，绝不喂给 Revit。
    min_len = MIN_WALL_SEGMENT_LEN
    p3 = [_xyz(q, elev) for q in seq]
    total = 0.0
    for i in range(len(p3) - 1):
        d = p3[i].DistanceTo(p3[i + 1])
        if d != d or d >= float('inf'):
            raise Exception(u"墙含非有限坐标（NaN/inf），已跳过")
        total += d
    # 整条墙过短直接整条跳过（剔除退化/噪声短墙）
    if total < MIN_WALL_TOTAL_LEN:
        raise Exception(u"墙总长过短（< %g mm），已跳过"
                        % (MIN_WALL_TOTAL_LEN / MM_TO_FT))
    for i in range(len(p3) - 1):
        if seq[i] == seq[i + 1]:
            continue
        pa, pb = p3[i], p3[i + 1]
        d = pa.DistanceTo(pb)
        if d != d or d >= float('inf') or d < min_len:
            continue
        curve = DB.Line.CreateBound(pa, pb)
        # Revit 2019 没有 4 参静态 Wall.Create（探针实证：曲线重载只有 8 参
        # 和 3 参无类型两种）。8 参形式（类型在前、楼层在后）是本机
        # 「写入诊断」按钮实测可用的活例。高度：计划给了用计划值，
        # 否则给 10ft（约 3m）缺省，避免 0 高墙。
        h_ft = (plan["height"] * MM_TO_FT) if (plan.get("height") or 0) > 0 else 10.0
        w = DB.Wall.Create(doc, curve, wt.Id, level_id, h_ft, 0.0, False, False)
        # v2.8d: 翻模墙两端禁止自动接合。Revit 会在创建时对端点相触的墙自动
        # 接合并清理转角（缩放/剪切剖面）——三崩实录里这是退化轮廓
        # （BlendSection non-unique plane / FaultyAtoms SWall）与「墙重叠」
        # 警告的发生器。接合纯属外观，安全优先；需要清理时用户手动「连接几何」。
        try:
            DB.WallUtils.DisallowWallJoinAtEnd(w, 0)
            DB.WallUtils.DisallowWallJoinAtEnd(w, 1)
        except Exception:
            pass
        if plan["height"] and plan["height"] > 0:
            p = w.get_Parameter(DB.BuiltInParameter.WALL_USER_HEIGHT_PARAM)
            if p:
                p.Set(plan["height"] * MM_TO_FT)
        created.append(w)
        if collect is not None:
            collect.append(w)
        if curves is not None:
            try:
                curves.append((w, w.Location.Curve))
            except Exception:
                pass
    if not created:
        raise Exception(u"墙的所有线段均退化（零长/近零/非有限），已跳过")
    return created[0]


def _create_column(doc, plan, level_id, sym):
    u"""柱：按定位点在指定楼层放一根结构柱。"""
    e = plan["geom"]
    p = _point_of(e)
    if not p:
        raise Exception(u"柱缺少定位点，已跳过")
    pt = _xyz(p, 0)
    lvl = doc.GetElement(level_id)
    if not sym or not lvl:
        raise Exception(u"柱缺少可用的族类型或楼层，已跳过")
    try:
        if not sym.IsActive:
            sym.Activate()
    except Exception:
        pass
    # Revit 2019 无静态 FamilyInstance.NewFamilyInstance（探针实证 MISSING）；
    # Creation.Document 的 (XYZ, FamilySymbol, Level, StructuralType) 重载存在。
    return doc.Create.NewFamilyInstance(
        pt, sym, lvl, DB.Structure.StructuralType.Column)


def _create_slab(doc, plan, level_id, ft):
    u"""楼板：闭合多段线建一块板；面积过小或有效边不足 3 则跳过。"""
    e = plan["geom"]
    pts = e.get("points") or []
    if not e.get("closed") or len(pts) < 3:
        raise Exception(u"楼板需要闭合多段线且至少 3 个点，已跳过")
    if not ft:
        raise Exception(u"项目中没有可用的楼板类型（FloorType），已跳过")
    elev = 0.0
    # 退化段跳过 + 面积过小时整片跳过
    area2 = 0.0
    ca = DB.CurveArray()
    p3 = [_xyz(p, elev) for p in pts]
    for i in range(len(p3)):
        pa, pb = p3[i], p3[(i + 1) % len(p3)]
        d = pa.DistanceTo(pb)
        if d != d or d >= float('inf') or d < MIN_WALL_SEGMENT_LEN:
            continue
        a, b = pts[i], pts[(i + 1) % len(pts)]
        area2 += (float(a[0]) * float(b[1])) - (float(b[0]) * float(a[1]))
        ca.Append(DB.Line.CreateBound(pa, pb))
    if abs(area2) < 1e3:  # < 约 1000 mm^2，视为退化板
        raise Exception(u"楼板面积过小（退化），已跳过")
    if ca.Size < 3:
        raise Exception(u"楼板有效边不足 3 条，已跳过")
    # Revit 2019 没有 Floor.Create 静态（探针实证 MISSING，2022 才有）；
    # doc.Create.NewFloor(CurveArray, FloorType, Level, structural) 是
    # model_builder 真机验证过的形式。
    return doc.Create.NewFloor(ca, ft, doc.GetElement(level_id), False)


def _create_point_symbol(doc, plan, level_id, sym, structural):
    u"""通用点式族实例（按定位点在楼层上放一个族）。"""
    e = plan["geom"]
    p = _point_of(e)
    if not p:
        raise Exception(u"点式族实例缺少定位点，已跳过")
    pt = _xyz(p, 0)
    return doc.Create.NewFamilyInstance(
        pt, sym, doc.GetElement(level_id), structural)


# --------------------------------------------------------------------------- #
# 楼梯（experimental）：DirectShape 阶梯实体
# --------------------------------------------------------------------------- #
_V211D = "stair-2arg-fallback"   # v2.11d 版本标记：CreateElement 2参回退（reload 校验用）
def _stair_footprint(e):
    u"""从楼梯图元推断 (跑长_mm, 宽度_mm)。

    只接受两类可建几何：① 多段线（楼梯轮廓，points>=2）；② 块插入（楼梯符号，仅插入点）。
    纯 LINE（天正楼梯的剖断线/上行箭头/踏步线，属注释）一律跳过，避免堆出大批细长占位盒。
    """
    pts = e.get("points") or []
    if len(pts) >= 2:
        xs = [p[0] for p in pts]
        ys = [p[1] for p in pts]
        w = max(xs) - min(xs)
        h = max(ys) - min(ys)
        run = max(w, h)
        width = min(w, h) if min(w, h) > 200 else 1200.0
        if not _num_ok(run) or not _num_ok(width) or run < 500:
            return None
        return run, width
    # 块插入：仅插入点（一个楼梯符号），用默认尺寸
    if e.get("point") or e.get("block"):
        return 3000.0, 1200.0
    # 纯 LINE（注释线）跳过
    return None


def _stair_category_id(doc):
    u"""取楼梯类别 Id（失败回退为按名字取）。"""
    try:
        return DB.Category.GetCategory(doc, DB.BuiltInCategory.OST_Stairs).Id
    except Exception:
        return doc.Settings.Categories.get_Item("Stairs").Id


def _stair_directshape(doc, cat_id):
    u"""v2.11d: Revit 2019 API 无 3 参 CreateElement(doc, cat, appId) 重载
    （实测报 'takes exactly 2 arguments (3 given)'），2 参优先、3 参回退。"""
    try:
        return DB.DirectShape.CreateElement(doc, cat_id, "bimtoolkit-stair")
    except Exception:
        return DB.DirectShape.CreateElement(doc, cat_id)


def _stair_solid(doc, e, L_mm, W_mm):
    u"""用台阶剖面拉伸生成阶梯实体（失败回退为长方体）。"""
    if not (_num_ok(L_mm) and _num_ok(W_mm) and L_mm > 0 and W_mm > 0):
        raise Exception(u"楼梯尺寸非有限或非法，已跳过")
    base = _xyz(_point_of(e) or [0, 0], 0)
    pts = e.get("points") or []
    if len(pts) >= 2:
        xs = [p[0] for p in pts]
        ys = [p[1] for p in pts]
        dx, dy = (max(xs) - min(xs)), (max(ys) - min(ys))
        d = DB.XYZ(0.0, 1.0, 0.0) if dy >= dx else DB.XYZ(1.0, 0.0, 0.0)
    else:
        d = DB.XYZ(1.0, 0.0, 0.0)
    w = DB.XYZ(-d.Y, d.X, 0.0)        # 宽度方向（水平、垂直于 d）
    up = DB.XYZ(0.0, 0.0, 1.0)
    n = min(50, max(2, int(round(L_mm / 280.0))))   # 每步约 280mm，上限 50 避免异常大数据
    sh = 167.0                                       # 每步高 167mm
    sr = L_mm / n
    H_mm = n * sh
    Wf = W_mm * MM_TO_FT

    def P(u, v):
        return base + d * (u * MM_TO_FT) + up * (v * MM_TO_FT)

    # 阶梯剖面（侧视），闭合多边形
    profile = [P(0, 0), P(L_mm, 0), P(L_mm, H_mm)]
    for k in range(n - 1, 0, -1):
        profile.append(P(k * sr, (k + 1) * sh))
        profile.append(P(k * sr, k * sh))
    profile.append(P(0, sh))
    profile.append(P(0, 0))

    def _loop_from_points(pts):
        u"""把点列转 CurveLoop，自动跳过退化/非有限边，返回 (loop, 有效曲线数)。"""
        loop = DB.CurveLoop()
        n = 0
        for i in range(len(pts) - 1):
            d = pts[i].DistanceTo(pts[i + 1])
            if d != d or d >= float('inf') or d < MIN_WALL_SEGMENT_LEN:
                continue
            loop.Append(DB.Line.CreateBound(pts[i], pts[i + 1]))
            n += 1
        return loop, n

    cat_id = _stair_category_id(doc)
    try:
        cl, n = _loop_from_points(profile)
        if n < 3:
            raise Exception(u"楼梯剖面有效边不足 3 条，已跳过")
        solid = DB.GeometryCreationUtilities.CreateExtrusionGeometry([cl], w, Wf)
        ds = _stair_directshape(doc, cat_id)
        ds.SetShape([solid])
        return ds
    except Exception:
        # 兜底：长方体（仍有体积占位）
        box = [P(0, 0), P(L_mm, 0), P(L_mm, H_mm), P(0, H_mm), P(0, 0)]
        cl, n = _loop_from_points(box)
        if n < 3:
            raise Exception(u"楼梯长方体剖面有效边不足 3 条，已跳过")
        solid = DB.GeometryCreationUtilities.CreateExtrusionGeometry([cl], w, Wf)
        ds = _stair_directshape(doc, cat_id)
        ds.SetShape([solid])
        return ds


def _create_stair(doc, plan, level_id):
    u"""楼梯：先推断尺寸再建 DirectShape 实体。"""
    e = plan["geom"]
    fp = _stair_footprint(e)
    if not fp:
        raise Exception(u"楼梯图元过小（疑似注释线），已跳过")
    return _stair_solid(doc, e, fp[0], fp[1])


# --------------------------------------------------------------------------- #
# 梁（2026-10-01 新增）
# --------------------------------------------------------------------------- #
# 原实现是直接跳过：`raise Exception("梁为实验性类型，本次跳过（需结构梁系统/线型族）")`。
# 但楼梯已经用 DirectShape 破了"必须有族"这个前提 —— 梁同样可以：
# 平面图里的梁就是「一条中线 + 截面宽高」，沿中线挤出长方体即可，不需要结构梁族。
#
# 已知取舍：
#   * 建出来的是**几何占位实体**（DirectShape），不是结构分析构件，
#     没有结构材质、不能参与结构计算、不会自动与柱墙连接。
#   * 截面宽高优先从 type_hint 里解析 "200x400"/"200*400"/"200X400"，
#     解析不到就用默认 200×400（可用 plan 的 beam_width_mm/beam_height_mm 覆盖）。
#   * 梁顶面在**楼层标高**上（即梁从本层楼面往上长 h），与"平面图里画的梁"一致。
_BEAM_DEFAULT_W_MM = 200.0
_BEAM_DEFAULT_H_MM = 400.0
import re as _re
_BEAM_SIZE_RE = _re.compile(r"(\d+(?:\.\d+)?)\s*[xX*×]\s*(\d+(?:\.\d+)?)")


def _beam_category_id(doc):
    u"""取结构框架类别 Id；取不到就回退楼梯类别（宁可进错类别也别丢构件）。"""
    for cat in ("OST_StructuralFraming",):
        try:
            return DB.Category.GetCategory(
                doc, getattr(DB.BuiltInCategory, cat)).Id
        except Exception:
            pass
    for name in (u"结构框架", u"Structural Framing"):
        try:
            return doc.Settings.Categories.get_Item(name).Id
        except Exception:
            pass
    return _stair_category_id(doc)


def _beam_directshape(doc, cat_id):
    try:
        return DB.DirectShape.CreateElement(doc, cat_id, u"bimtoolkit-beam")
    except Exception:
        return DB.DirectShape.CreateElement(doc, cat_id)


def _beam_size(plan):
    u"""返回 (宽mm, 高mm)。优先显式字段，其次从 type_hint 解析，最后默认。"""
    w = plan.get("beam_width_mm")
    h = plan.get("beam_height_mm")
    if _num_ok(w) and _num_ok(h) and float(w) > 0 and float(h) > 0:
        return float(w), float(h)
    hint = plan.get("type_hint")
    m = _BEAM_SIZE_RE.search(u"%s" % (hint or u""))
    if m:
        a, b = float(m.group(1)), float(m.group(2))
        if a > 0 and b > 0:
            # 约定：写 "200x400" 时前者是宽、后者是高
            return a, b
    return _BEAM_DEFAULT_W_MM, _BEAM_DEFAULT_H_MM


def _level_elevation_ft(doc, level_id):
    u"""楼层标高（英尺）。取不到返回 0.0。"""
    try:
        lv = doc.GetElement(level_id)
        return float(lv.Elevation)
    except Exception:
        return 0.0


def _create_beam(doc, plan, level_id):
    u"""梁：沿梁中线挤出长方体（DirectShape，无需结构梁族）。"""
    e = plan["geom"] or {}
    start, end = e.get("start"), e.get("end")
    if not start or not end:
        raise Exception(u"梁图元缺少起止点，已跳过")

    # 端点合法性由 _xyz 校验（NaN/inf/越界一律抛，由上层记为跳过）
    elev_ft = _level_elevation_ft(doc, level_id)
    a = DB.XYZ(float(start[0]) * MM_TO_FT, float(start[1]) * MM_TO_FT, elev_ft)
    b = DB.XYZ(float(end[0]) * MM_TO_FT, float(end[1]) * MM_TO_FT, elev_ft)
    if not (_num_ok(start[0]) and _num_ok(start[1])
            and _num_ok(end[0]) and _num_ok(end[1])):
        raise Exception(u"梁端点含非有限/越界数值，已跳过")

    dx, dy = (float(end[0]) - float(start[0])), (float(end[1]) - float(start[1]))
    length_mm = (dx * dx + dy * dy) ** 0.5
    if length_mm < MIN_WALL_SEGMENT_LEN or length_mm != length_mm:
        raise Exception(u"梁长度过短或非有限（%.1fmm），已跳过" % length_mm)
    # 单位方向（水平）
    ux, uy = dx / length_mm, dy / length_mm

    w_mm, h_mm = _beam_size(plan)
    Lft = length_mm * MM_TO_FT
    Wft = w_mm * MM_TO_FT
    Hft = h_mm * MM_TO_FT

    d = DB.XYZ(ux, uy, 0.0)              # 沿梁方向
    wv = DB.XYZ(-uy, ux, 0.0)            # 水平法向（梁宽方向）
    up = DB.XYZ(0.0, 0.0, 1.0)
    # 让梁**以中线居中**：剖面平面从起点的中线位置沿宽方向偏移半个梁宽
    base = a - wv * (Wft / 2.0)

    def P(u_ft, v_ft):
        return base + d * u_ft + up * v_ft

    profile = [P(0, 0), P(Lft, 0), P(Lft, Hft), P(0, Hft), P(0, 0)]

    loop = DB.CurveLoop()
    n = 0
    for i in range(len(profile) - 1):
        dist = profile[i].DistanceTo(profile[i + 1])
        if dist != dist or dist >= float('inf') or dist < 1e-9:
            continue
        loop.Append(DB.Line.CreateBound(profile[i], profile[i + 1]))
        n += 1
    if n < 3:
        raise Exception(u"梁剖面有效边不足 3 条，已跳过")

    solid = DB.GeometryCreationUtilities.CreateExtrusionGeometry([loop], wv, Wft)
    ds = _beam_directshape(doc, _beam_category_id(doc))
    ds.SetShape([solid])
    return ds


# --------------------------------------------------------------------------- #
# 门 / 窗（寄宿到墙并开洞）
# --------------------------------------------------------------------------- #
# v2.11d: 寄宿距离上限 1500→2000mm。实测依据（diag_dw53，3f046c573d）：
# 门检出 bbox 含开启弧，中心被推离墙面约半宽（1-1.5m 桶 113 个、0.5-1m 桶 0 个，
# 系统性偏移），1.5m 门槛误杀 93 扇可寄宿门。放宽后投影点仍在墙上，语义无损。
_HOST_MAX_DIST_MM = 2000.0
_V211D = "host-dist-2000"   # reload 校验标记


def _create_door_window(doc, plan, level_id, sym, wall_index, cut=True):
    u"""门/窗：寄宿到最近墙并自动开洞（cut=True）；cut=False 时按点+楼层自由放置（不切洞）。

    寄宿用 doc.Create.NewFamilyInstance(点, 族类型, 宿主墙, 楼层, NonStructural)
    （自动开洞；Create Villa 真机验证过的 5 参形式。Revit 2019 没有
    FamilyInstance.NewFamilyInstance 静态方法，探针实证 MISSING）。
    自由放置用 doc.Create.NewFamilyInstance(点, 族类型, 楼层, NonStructural)。
    """
    e = plan["geom"]
    p = _point_of(e)
    if not p:
        raise Exception(u"门/窗缺少定位点，已跳过")
    pt = _xyz(p, 0)
    if not cut:
        # 不寄宿、不切洞：避免大批量布尔切孔把 Revit 拖崩；失败则跳过
        lvl = doc.GetElement(level_id)
        if lvl is None:
            raise Exception(u"门/窗自由放置失败：找不到楼层")
        if sym is None:
            raise Exception(u"门/窗自由放置失败：缺少族类型")
        try:
            if not sym.IsActive:
                sym.Activate()
        except Exception:
            pass
        return doc.Create.NewFamilyInstance(
            pt, sym, lvl, DB.Structure.StructuralType.NonStructural)
    if wall_index:
        buckets, key = wall_index
        best, best_c, best_d = _nearest_wall(pt, buckets, key)
    else:
        best = None
        best_c = None
        best_d = 1e9
    if best is None:
        raise Exception(u"模型中无墙可寄宿，门/窗未建模")
    if best_d > _HOST_MAX_DIST_MM * MM_TO_FT:
        # 距任何墙都过远：多为窗表/标注/散落图块，跳过以免生成游离实例
        raise Exception(u"门/窗距最近墙 %.0f mm，未寄宿开洞（非墙体开洞），已跳过"
                        % (best_d / MM_TO_FT))
    # v2.5 宿主墙长度预检：符号宽于宿主墙段时 Revit 会 post「无法生成洞口」错误，
    # 真图实测（05.dwg，2026-09-11）两条 post + 提交期失败处理原生崩溃
    # （journal: ADocumentFailure.h:389）。宁缺勿崩：放不下的直接跳过。
    _wlen = None
    try:
        _wlen = best_c.Length
    except Exception:
        _wlen = None
    _w = None
    try:
        if hasattr(sym, "LookupParameter"):
            for _nm in (u"宽度", "Width"):
                _p = sym.LookupParameter(_nm)
                if _p is not None and _p.HasValue:
                    _v = _p.AsDouble()
                    if _v and _v > 0:
                        _w = _v
                        break
    except Exception:
        _w = None
    if _wlen is not None and _w is not None and _wlen < _w + 0.1:
        raise Exception(u"宿主墙段长 %.0fmm 小于门/窗宽 %.0fmm，开洞必失败，已跳过"
                        % (_wlen / MM_TO_FT, _w / MM_TO_FT))
    try:
        proj = best_c.Project(pt)
        loc = proj.XYZ if proj else pt
        ref_dir = best_c.GetEndPoint(1) - best_c.GetEndPoint(0)
        ref_dir = ref_dir.Normalize()
    except Exception:
        loc = pt
        ref_dir = DB.XYZ(1.0, 0.0, 0.0)
    # 寄宿点必须有限，否则 NewFamilyInstance 喂入 NaN 会触发 Revit 原生崩溃
    for _c in (loc.X, loc.Y, loc.Z):
        if _c != _c or abs(_c) >= float('inf'):
            raise Exception(u"门/窗寄宿点含非有限坐标，已跳过")
    if sym is None:
        raise Exception(u"门/窗寄宿失败：缺少族类型")
    try:
        if not sym.IsActive:
            sym.Activate()
    except Exception:
        pass
    # 2019 无静态 (宿主, 点, 方向, 符号) 重载；改用 Create Villa 实测可用的
    # 5 参 (点, 符号, 宿主墙, 楼层, NonStructural)。参考方向弃用——朝向由墙向决定。
    return doc.Create.NewFamilyInstance(loc, sym, best,
                                        doc.GetElement(level_id),
                                        DB.Structure.StructuralType.NonStructural)


# --------------------------------------------------------------------------- #
# 房间（experimental）
# --------------------------------------------------------------------------- #
def _create_room(doc, plan, level_id, view_cache=None):
    u"""房间：取目标楼层对应的平面视图，在闭合多段线中心放一个 Room（尽力而为）。

    view_cache: {level_id_int: ViewPlan} 预取缓存，避免逐房间全库扫描。
    加强校验：闭合性 + 面积 >= 2 m² + 质心非负，过滤注释线产生的伪房间。
    """
    pts = plan["geom"].get("points") or []
    if len(pts) < 3:
        raise Exception(u"房间需要闭合多段线且至少 3 个点，已跳过")
    for p in pts:
        if not (isinstance(p, (list, tuple)) and len(p) >= 2
                and _num_ok(p[0]) and _num_ok(p[1])):
            raise Exception(u"房间含非有限/越界坐标，已跳过")
    closed = plan["geom"].get("closed", False)
    if closed and len(pts) >= 3:
        fx, fy = float(pts[0][0]), float(pts[0][1])
        lx, ly = float(pts[-1][0]), float(pts[-1][1])
        if abs(fx - lx) > 1.0 or abs(fy - ly) > 1.0:
            raise Exception(u"房间轮廓未闭合（首尾差距 > 1mm），已跳过")
    area2 = 0.0
    for i in range(len(pts)):
        x1, y1 = float(pts[i][0]), float(pts[i][1])
        x2, y2 = float(pts[(i + 1) % len(pts)][0]), float(pts[(i + 1) % len(pts)][1])
        area2 += (x1 * y2) - (x2 * y1)
    area_mm2 = abs(area2) / 2.0
    if area_mm2 < 4e6:
        raise Exception(u"房间面积过小（< 2 m²，疑似注释线），已跳过")
    cx = sum(float(p[0]) for p in pts) / len(pts)
    cy = sum(float(p[1]) for p in pts) / len(pts)
    if cx < -1e5 or cy < -1e5 or cx > 1e7 or cy > 1e7:
        raise Exception(u"房间质心超出建筑范围（疑似详图线），已跳过")
    if view_cache is not None:
        view = view_cache.get(level_id.IntegerValue if level_id else None)
    else:
        view = None
        for v in DB.FilteredElementCollector(doc).OfClass(DB.ViewPlan).ToElements():
            if v.GenLevel and v.GenLevel.Id == level_id:
                view = v
                break
    if view is None:
        raise Exception(u"找不到该楼层对应的平面视图，已跳过")
    room = doc.Create.NewRoom(view, DB.UV(cx * MM_TO_FT, cy * MM_TO_FT))
    return room
