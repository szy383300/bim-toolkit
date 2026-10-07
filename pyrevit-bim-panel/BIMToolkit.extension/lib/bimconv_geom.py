# -*- coding: utf-8 -*-
u"""
bimconv_geom.py —— 翻模底层：单位换算 + 坐标校验 + 退化守卫。

职责：只管「数值是否安全、点怎么转成 Revit 坐标」，不碰任何 Revit 构件创建。

为什么要单独成层（本项目最关键的防崩溃设计）：
  脏 DWG 常含 NaN / inf / 1e308 这类越界坐标。这类坏点一旦喂给 Revit API
  （DB.Wall.Create / Floor.Create / NewFamilyInstance），会触发 **原生崩溃** ——
  Python 的 try/except 捕获不到，表现为「Revit 遇到了意外错误」闪退、整笔事务
  被回滚、零构件落盘（即「跑完没模型、软件自己关了」）。

  更隐蔽的坑：退化守卫原来写成 ``if d < min_len: skip``，但 **NaN < 任何值都为 False**，
  坏点反而绕过守卫直接进 Revit。所以本层统一用「除非明确合格否则跳过」的判定：
  ``d != d``（NaN）/ ``d >= inf`` / ``d < min_len`` 三者任一成立即跳过。

所有上层模块必须经本层取点，禁止直接把原始坐标传给 Revit API。
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


# 毫米 → 英尺（Revit 内部单位）
MM_TO_FT = 1.0 / 304.8

# 退化几何阈值（思路参考开源 OdedCas/mytools-pyrevit 的可配置 min_wall_len_cm）：
# 单段墙曲线短于此值视为退化直接跳过；整条墙总长也设下限以剔除噪声短墙。
# 按图纸精度可上调（噪点多的图可改到 5~10mm）。
MIN_WALL_SEGMENT_LEN = 10.0 * MM_TO_FT   # 单段 < 10mm 视为退化
MIN_WALL_TOTAL_LEN = 50.0 * MM_TO_FT     # 整条墙总长 < 50mm 整条跳过


def _num_ok(v):
    u"""坐标数值有效性：必须是有限实数，且落在建筑合理范围（|mm| <= 1e7，约 10km）。

    拦截脏 DWG 里的 NaN / inf / 越界坏点（废块、样条、1e308 坐标等）。
    IronPython 2.7 没有 math.isfinite，故用 ``v != v`` 判 NaN、``abs(v) >= inf`` 判 inf。
    """
    try:
        f = float(v)
    except Exception:
        return False
    if f != f:                  # NaN
        return False
    if abs(f) >= float('inf'):  # inf
        return False
    if abs(f) > 1e7:            # 超出建筑尺度，视为坏点
        return False
    return True


def _xyz(p, elev=0.0):
    u"""把毫米坐标点转成 Revit 的 XYZ（英尺）。坐标非法直接抛，由上层记为「跳过」。"""
    if not isinstance(p, (list, tuple)) or len(p) < 2:
        raise Exception(u"坐标点至少需要 x,y 两个值")
    if not (_num_ok(p[0]) and _num_ok(p[1])):
        raise Exception(u"坐标点含非有限/越界数值（NaN/inf/过大），已跳过")
    if not _num_ok(elev):
        raise Exception(u"标高含非有限数值（NaN/inf/过大），已跳过")
    return DB.XYZ(float(p[0]) * MM_TO_FT, float(p[1]) * MM_TO_FT,
                  float(elev) * MM_TO_FT)


def _point_of(e):
    u"""取实体的定位点：优先块插入点，其次线段中点，再次多段线包围盒形心。

    天正图的墙/柱/窗常以 LINE 线段表示（只有 start/end，没有 point），
    用线段中点作放置点，保证规则命中后仍能落构件。
    V2.3c：柱/门等常以闭合轮廓多段线绘制（如 COLU_TH 柱轮廓），
    取包围盒形心作定位点，让这类实体也能落构件。
    """
    if e.get("point"):
        return e["point"]
    s, en = e.get("start"), e.get("end")
    if s and en:
        return [(a + b) / 2.0 for a, b in zip(s, en)]
    pts = e.get("points") or []
    if pts:
        try:
            xs = [float(p[0]) for p in pts]
            ys = [float(p[1]) for p in pts]
            return [(min(xs) + max(xs)) / 2.0, (min(ys) + max(ys)) / 2.0]
        except Exception:
            return None
    return None
