# -*- coding: utf-8 -*-
u"""
bimconv_walls.py —— 翻模「墙线处理」层：双线轮廓取中、零散墙段链成连续墙线、墙段接合、门/窗寄宿索引。

四块内容：
  0) _outline_centerline_plans()  把「闭合双线墙轮廓」(真图 05.dwg 实锤：WALL 层的多段线
     全是 closed=True 的墙外形，自带真实墙厚) 还原成沿长轴的单墙中心线，厚度实测携带。
     旧逻辑把轮廓当中心线逐段建墙 → 拐角长出微型墙桩互相重叠 → Revit
     「无法使图元保持连接」逐件回滚（实锤 13/18 回滚）。
  1) consolidate_walls()  把 DWG 里零散的 LINE 墙段按端点链成连续折线，
     大幅减少构件数，并让 Revit 自动清理相交、为 Join 提供干净端点。
  2) _join_walls()        用 JoinGeometryUtils 把共享端点的墙接合（最重几何运算之一）。
  3) _build_wall_index() / _nearest_wall()
                          墙面空间哈希索引，供门/窗就近寄宿快速查找宿主墙。

依赖：bimconv_geom（单位换算 / 取点）。
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

from bimconv_geom import MM_TO_FT, MIN_WALL_TOTAL_LEN, _point_of
from collections import defaultdict
import math


# 端点合并容差（毫米）：链墙线段时，两端点距离在此容差内视为同一个点
_MERGE_TOL_MM = 2.0


# --------------------------------------------------------------------------- #
# 1) 零散墙段 → 连续墙线（纯几何，可 headless 测）
# --------------------------------------------------------------------------- #
def _wall_segments(plan):
    u"""把一个墙 plan 的几何拆成若干 (p1, p2) 线段（毫米坐标）。"""
    e = plan["geom"]
    pts = e.get("points") or []
    if len(pts) >= 2:
        seq = pts + ([pts[0]] if e.get("closed") else [])
        return [((seq[i][0], seq[i][1]), (seq[i + 1][0], seq[i + 1][1]))
                for i in range(len(seq) - 1)]
    s, en = e.get("start"), e.get("end")
    if s and en:
        return [((s[0], s[1]), (en[0], en[1]))]
    p = _point_of(e)
    if p:
        return [((p[0], p[1]), (p[0], p[1]))]
    return []


def _simplify_collinear(pts):
    u"""去掉共线内部点，让折线更干净（端点保留）。"""
    if len(pts) < 3:
        return pts
    out = [pts[0]]
    for i in range(1, len(pts) - 1):
        a, b, c = pts[i - 1], pts[i], pts[i + 1]
        cross = (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])
        if abs(cross) > 1e-6:
            out.append(b)
    out.append(pts[-1])
    return out


def _chain_segments(segs):
    u"""把零散线段按共享端点链成连续折线（合并共线内部点）。"""
    def key(p):
        return (int(round(p[0] / _MERGE_TOL_MM)), int(round(p[1] / _MERGE_TOL_MM)))

    adj = {}
    for i, (a, b) in enumerate(segs):
        adj.setdefault(key(a), []).append((i, 0))
        adj.setdefault(key(b), []).append((i, 1))

    def find_unvisited(k):
        for (si, endn) in adj.get(k, []):
            if not visited[si]:
                return si, endn
        return None

    visited = [False] * len(segs)
    polys = []
    for i in range(len(segs)):
        if visited[i]:
            continue
        a, b = segs[i]
        chain = [list(a), list(b)]
        visited[i] = True
        cur = b
        while True:
            nxt = find_unvisited(key(cur))
            if nxt is None:
                break
            si, endn = nxt
            visited[si] = True
            oa, ob = segs[si]
            other = ob if endn == 0 else oa
            chain.append(list(other))
            cur = other
        cur = a
        while True:
            nxt = find_unvisited(key(cur))
            if nxt is None:
                break
            si, endn = nxt
            visited[si] = True
            oa, ob = segs[si]
            other = ob if endn == 0 else oa
            chain.insert(0, list(other))
            cur = other
        polys.append(_simplify_collinear(chain))
    return polys


# --------------------------------------------------------------------------- #
# 0) 闭合双线墙轮廓 → 单墙中心线（v2.8，真图 05.dwg 实锤驱动的核心修复）
# --------------------------------------------------------------------------- #
# 薄轮廓判定：实测厚度超出此区间视为「房间轮廓/厚实体」等，回退旧的逐段逻辑，
# 不做任何劣化。40mm 下限挡注释线噪声；600mm 上限挡剪力墙筒/房间外包框。
_OUTLINE_MIN_THICK_MM = 40.0
_OUTLINE_MAX_THICK_MM = 600.0
_RECT_TOL_MM = 1.0            # 轴对齐判定容差（mm）
_MERGE_EPS_MM = 0.5           # 矩形相邻合并容差（mm）


def _ring_of(plan):
    u"""取闭合多段线环（毫米 float 二元组列表）；非闭合/点数不足返回 None。"""
    e = plan["geom"]
    if not e.get("closed"):
        return None
    pts = e.get("points") or []
    if len(pts) < 4:
        return None
    try:
        return [(float(p[0]), float(p[1])) for p in pts]
    except Exception:
        return None


def _is_rectilinear(ring, tol=_RECT_TOL_MM):
    u"""所有边是否轴对齐（水平或垂直，容差 tol mm）。弧墙/斜墙直接 False → 回退。"""
    n = len(ring)
    for i in range(n):
        x1, y1 = ring[i]
        x2, y2 = ring[(i + 1) % n]
        if abs(x2 - x1) > tol and abs(y2 - y1) > tol:
            return False
    return True


def _decompose_rects(ring):
    u"""轴对齐多边形 → 竖直扫描线切成矩形列表 [(x0, ya, x1, yb)]。

    对每个相邻顶点 x 区间的中点作竖直线，与多边形所有水平边求交，
    交点 y 排序后两两配对即该条带内的 y 区间 → 每个区间一个矩形。
    L/T/U 形墙自动被切成若干矩形跑段。
    """
    xs = sorted(set(p[0] for p in ring))
    rects = []
    n = len(ring)
    for i in range(len(xs) - 1):
        x0, x1 = xs[i], xs[i + 1]
        if x1 - x0 < 1e-6:
            continue
        xm = (x0 + x1) / 2.0
        ys = []
        for j in range(n):
            ax, ay = ring[j]
            bx, by = ring[(j + 1) % n]
            if abs(ax - bx) < 1e-6:
                continue  # 竖直边与竖直扫描线无独立交点
            lo, hi = (ax, bx) if ax < bx else (bx, ax)
            if lo < xm < hi:
                ys.append(ay)
        ys.sort()
        for j in range(0, len(ys) - 1, 2):
            ya, yb = ys[j], ys[j + 1]
            if yb - ya < 1e-6:
                continue
            rects.append((x0, ya, x1, yb))
    return rects


def _merge_rects(rects, eps=_MERGE_EPS_MM):
    u"""相邻同区间矩形合并成极大矩形：先按行（同 ya,yb 并相邻 x），再按列。

    只合并「严格相邻」（间距 ≤ eps）的矩形——隔空的同区间矩形是两道独立的墙，
    绝不能跨缝焊死。
    """
    # 行合并
    rows = {}
    for (x0, ya, x1, yb) in rects:
        rows.setdefault((round(ya, 3), round(yb, 3)), []).append((x0, x1))
    row_merged = []
    for (ya, yb), spans in rows.items():
        spans.sort()
        cx0, cx1 = spans[0]
        for s0, s1 in spans[1:]:
            if s0 <= cx1 + eps:
                cx1 = max(cx1, s1)
            else:
                row_merged.append((cx0, ya, cx1, yb))
                cx0, cx1 = s0, s1
        row_merged.append((cx0, ya, cx1, yb))
    # 列合并
    cols = {}
    for (x0, ya, x1, yb) in row_merged:
        cols.setdefault((round(x0, 3), round(x1, 3)), []).append((ya, yb))
    out = []
    for (x0, x1), spans in cols.items():
        spans.sort()
        cy0, cy1 = spans[0]
        for s0, s1 in spans[1:]:
            if s0 <= cy1 + eps:
                cy1 = max(cy1, s1)
            else:
                out.append((x0, cy0, x1, cy1))
                cy0, cy1 = s0, s1
        out.append((x0, cy0, x1, cy1))
    return out


def _outline_centerline_plans(plan):
    u"""闭合薄轮廓 → 单墙中心线 plan 列表；不适用的轮廓返回 None（调用方回退旧逻辑）。

    产出：每个极大矩形跑段一条单段直墙 plan，携带实测厚度 thickness_mm（毫米 int），
    由 build 侧选/建对应厚度墙类型。非轴对齐（弧/斜）与厚轮廓一律回退，绝不劣化。
    """
    ring = _ring_of(plan)
    if ring is None or not _is_rectilinear(ring):
        return None
    rects = _merge_rects(_decompose_rects(ring))
    if not rects:
        return None
    thk = min(min(r[2] - r[0], r[3] - r[1]) for r in rects)
    if thk < _OUTLINE_MIN_THICK_MM or thk > _OUTLINE_MAX_THICK_MM:
        return None
    thk_i = int(round(thk))
    out = []
    for (x0, ya, x1, yb) in rects:
        w, h = x1 - x0, yb - ya
        if max(w, h) < 2.5 * thk:
            continue  # 近方块（拐角残留），跳过——真实跑段由相邻矩形承担
        if w >= h:
            pts = [[x0, (ya + yb) / 2.0], [x1, (ya + yb) / 2.0]]
        else:
            pts = [[(x0 + x1) / 2.0, ya], [(x0 + x1) / 2.0, yb]]
        np_ = dict(plan)
        g = dict(plan["geom"])
        g["type"] = "lwpolyline"
        g["points"] = pts
        g["closed"] = False
        np_["geom"] = g
        np_["thickness_mm"] = thk_i
        np_["note"] = (plan.get("note") or u"") + u"｜轮廓取中 t=%d" % thk_i
        out.append(np_)
    if not out:
        return None
    return out


def _build_wall_plans(wall_plans, others):
    u"""按 (楼层, 层高, 类型, 厚度) 分组链化墙 plan，输出 merged 列表（含非墙 plan 原样透传）。"""
    groups = {}
    for q in wall_plans:
        key = (q["level"], q["height"], q["type_hint"], q.get("thickness_mm"))
        groups.setdefault(key, []).append(q)
    merged = list(others)
    for key, plans in groups.items():
        segs = []
        for p in plans:
            segs.extend(_wall_segments(p))
        segs = [s for s in segs if s[0] != s[1]]  # 去掉零长段
        if not segs:
            continue
        base = plans[0]
        for poly in _chain_segments(segs):
            if len(poly) < 2:
                continue
            rec = {
                "element_type": "wall",
                "reliability": base["reliability"],
                "level": key[0],
                "height": key[1],
                "type_hint": key[2],
                # v2.14: 合并后的墙仍要能回答"这层是怎么定的"——图框路由
                # 的依据不能在链化后丢失，预览的定层报表靠它
                "level_basis": base.get("level_basis") or u"",
                "source": base["source"],
                "geom": {"type": "lwpolyline",
                         "points": [list(pp) for pp in poly],
                         "closed": False,
                         "layer": base["geom"].get("layer", "")},
                "note": base["note"],
            }
            if key[3]:
                rec["thickness_mm"] = key[3]
                rec["note"] = (rec["note"] or u"") + u"｜t=%d" % key[3]
            merged.append(rec)
    return merged


# ---- v2.8b: 墙重叠消解（真图 05.dwg 原生崩溃实录驱动）----
# 崩溃链：回退链化的重复墙线（40~120mm 间距的平行线）+ 中心线 T/L 角未裁
# → 38 对带厚重叠 → Revit「墙重叠」失败风暴 → 删警告+ProceedWithCommit
# → 重生成再挂 → 无限重试 → Revit 666 轮强制终止 → 原生崩溃。
# 消解两层：同向同带重复线去重（保最长）+ 端部伸入他墙厚度带的裁到贴面（butt joint）。
_OVERLAP_AXIS_FRAC = 0.55   # 同带判定：轴线间距 < min(t)*此系数（近似同心才判重）
_OVERLAP_LEN_FRAC = 0.5     # 轴向重叠 > 短墙长度的此比例才判重（真实平行墙罕达）
_V211D = "diag-pass-through"   # v2.11d 版本标记：斜线透传修复已加载（reload 校验用）
_V211F = "rechain"             # v2.11f 版本标记：碎片墙再链化已加载（reload 校验用）


def _seg_axis_rect(p):
    u"""单段墙 plan → 归一化 (xa0, ya0, xa1, ya1, t, dir)；多段/斜墙返回 None。

    v2.8b: 端点必须归一化（min/max）——真图右单元的墙线是反序存储
    ([26580..],[23520..])，不归一化则重叠判定算出负面积，整组叠墙漏网。
    """
    pts = p["geom"].get("points") or []
    if len(pts) != 2:
        return None
    try:
        x1, y1 = float(pts[0][0]), float(pts[0][1])
        x2, y2 = float(pts[1][0]), float(pts[1][1])
    except Exception:
        return None
    if abs(x1 - x2) > _RECT_TOL_MM and abs(y1 - y2) > _RECT_TOL_MM:
        return None
    t = float(p.get("thickness_mm") or 240.0)
    d = 'H' if abs(y1 - y2) <= _RECT_TOL_MM else 'V'
    return (min(x1, x2), min(y1, y2), max(x1, x2), max(y1, y2), t, d)


def _resolve_wall_overlaps(merged):
    u"""链化结果去重 + 裁角，消除带厚矩形两两重叠。纯 Python，可 headless 测。"""
    walls = [p for p in merged if p["element_type"] == "wall"]
    others = [p for p in merged if p["element_type"] != "wall"]

    # 拆成单段（多段链拆开逐段处理，保留 plan 元数据）
    # v2.11d: 斜线段收集透传——v2.8b 原版 _seg_axis_rect 返回 None 的斜墙被静默
    # 丢弃，斜坐标系图（实测 c12b1ae652：原生墙 574 根中 78% 在 20~40°）全灭
    # （965/973 墙湮灭实录）。斜段不参与轴对齐去重/裁角，末尾随 keep 重新链化。
    items = []
    diag_segs = []
    for p in walls:
        pts = p["geom"].get("points") or []
        seq = pts + ([pts[0]] if (p["geom"].get("closed") and pts) else [])
        for k in range(len(seq) - 1):
            a, b = seq[k], seq[k + 1]
            q = dict(p)
            g = dict(p["geom"])
            g["points"] = [list(a), list(b)]
            g["closed"] = False
            q["geom"] = g
            r = _seg_axis_rect(q)
            if r:
                items.append([q, r])
            else:
                diag_segs.append(q)
    if not items:
        if not diag_segs:
            return merged
        return _build_wall_plans(diag_segs, others)

    # 1) 同向同带重复线去重：轴线近同心 + 轴向大比例重叠 → 保最长、弃其余
    order = sorted(range(len(items)),
                   key=lambda i: -((items[i][1][2] - items[i][1][0]) +
                                   (items[i][1][3] - items[i][1][1])))
    drop = [False] * len(items)
    # 分区键：这段去重**必须看楼层**。多层图里上下两层的同坐标墙在 2D 上完全同心，
    # 不看就会把一层整层删掉（合成真值「跨楼层不得串元数据」实测 2 层塌成 1 面）。
    part = [(it[0].get("level", u""), it[0].get("height", 0),
             it[0].get("type_hint", u"")) for it in items]
    for a in range(len(order)):
        ia = order[a]
        if drop[ia]:
            continue
        ra = items[ia][1]
        la = abs(ra[2] - ra[0]) + abs(ra[3] - ra[1])
        for b in range(a + 1, len(order)):
            ib = order[b]
            if drop[ib] or part[ia] != part[ib]:
                continue
            rb = items[ib][1]
            if ra[5] != rb[5]:
                continue
            lb = abs(rb[2] - rb[0]) + abs(rb[3] - rb[1])
            if ra[5] == 'H':
                axis_gap = abs((ra[1] + ra[3]) - (rb[1] + rb[3])) * 0.5
                ov = min(ra[2], rb[2]) - max(ra[0], rb[0])
            else:
                axis_gap = abs((ra[0] + ra[2]) - (rb[0] + rb[2])) * 0.5
                ov = min(ra[3], rb[3]) - max(ra[1], rb[1])
            if axis_gap * 2.0 < ra[4] + rb[4] and \
                    ov > lb * _OVERLAP_LEN_FRAC:
                drop[ib] = True  # 短者淘汰
    items = [it for i, it in enumerate(items) if not drop[i]]

    # 2) 端部伸入他墙（异向）厚度带 → 裁到贴面（butt joint）
    #    端点直接读 plan 原始点序（rect 已归一化，不再对应端点顺序）
    for i in range(len(items)):
        p, r = items[i]
        xa0, ya0, xa1, ya1, t, d = r
        L = (xa1 - xa0) + (ya1 - ya0)
        if L <= t:
            continue  # 短跑段不裁（裁了就没了）
        pts = p["geom"]["points"]
        for e in (0, 1):
            ex, ey = float(pts[e][0]), float(pts[e][1])
            oe = 1 - e
            ox, oy = float(pts[oe][0]), float(pts[oe][1])
            for j in range(len(items)):
                if j == i:
                    continue
                q = items[j][1]
                if q[5] == d:
                    continue
                if d == 'H' and q[5] == 'V':
                    qx0 = q[0] - q[4] / 2.0
                    qx1 = q[2] + q[4] / 2.0
                    qy0 = q[1] - q[4] / 2.0
                    qy1 = q[3] + q[4] / 2.0
                    if qx0 + 1.0 < ex < qx1 - 1.0 and qy0 <= ey <= qy1:
                        nx = qx0 if ox < ex else qx1
                        if abs(nx - ox) > t:  # 裁后仍是有效跑段
                            pts[e] = [nx, ey]
                            ex = nx
                elif d == 'V' and q[5] == 'H':
                    qy0 = q[1] - q[4] / 2.0
                    qy1 = q[3] + q[4] / 2.0
                    qx0 = q[0] - q[4] / 2.0
                    qx1 = q[2] + q[4] / 2.0
                    if qy0 + 1.0 < ey < qy1 - 1.0 and qx0 <= ex <= qx1:
                        ny = qy0 if oy < ey else qy1
                        if abs(ny - oy) > t:
                            pts[e] = [ex, ny]
                            ey = ny

        # 2c) 同向近带残余（真图实锤：栏板 vs 错位实体墙，厚度带相交但轴向只搭一点）
        #     短者的搭接端裁到长者的端面，消掉平行带重叠
        r = items[i][1]
        for j in range(len(items)):
            if j == i:
                continue
            q = items[j][1]
            if q[5] != r[5]:
                continue
            if r[5] == 'H':
                gap = abs(((r[1] + r[3]) - (q[1] + q[3])) * 0.5)
            else:
                gap = abs(((r[0] + r[2]) - (q[0] + q[2])) * 0.5)
            if gap * 2.0 >= r[4] + q[4]:
                continue  # 厚度带不相交
            lr = abs(r[2] - r[0]) + abs(r[3] - r[1])
            lq = abs(q[2] - q[0]) + abs(q[3] - q[1])
            if lr >= lq:
                continue  # 只裁短者（长者自己的循环里不会裁它）
            pts = p["geom"]["points"]
            for e in (0, 1):
                ex, ey = float(pts[e][0]), float(pts[e][1])
                if r[5] == 'H' and q[0] + 1.0 < ex < q[2] - 1.0:
                    nx = q[0] if ex < (q[0] + q[2]) * 0.5 else q[2]
                    pts[e] = [nx, ey]
                elif r[5] == 'V' and q[1] + 1.0 < ey < q[3] - 1.0:
                    ny = q[1] if ey < (q[1] + q[3]) * 0.5 else q[3]
                    pts[e] = [ex, ny]

    # 3) 裁后去退化再重新链化：
    #    - 长度 < max(50, t)：退化噪声
    #    - v2.8c: 长度 < 1.6×t 的短粗墙垛直接剔除——真图 05.dwg 二次崩溃实录：
    #      19 面 L≈t 的正方形墙垛（240×240 等）喂给 Revit 接合/清理 →
    #      BlendSection non-unique plane → FaultyAtomsCheck SWall → 几何核原生崩溃。
    #      这些垛子本是窗间墙墩，剔除后的缺口就是洞口，模型语义不受损。
    keep = []
    for p, r in items:
        x1, y1, x2, y2, t, d = r
        pts = p["geom"]["points"]
        nx1, ny1 = float(pts[0][0]), float(pts[0][1])
        nx2, ny2 = float(pts[1][0]), float(pts[1][1])
        L = ((nx1 - nx2) ** 2 + (ny1 - ny2) ** 2) ** 0.5
        if L < max(50.0, t):
            continue
        if L < 1.6 * t:
            continue
        keep.append(p)
    # v2.11d: 斜线段随轴对齐存活段一起重新链化（不再静默丢弃）
    return _build_wall_plans(keep + diag_segs, others)


def _angle_groups(items):
    u"""按角度(mod 180°)贪心分簇；首尾簇可能环绕同簇。"""
    items = sorted(items, key=lambda d: d[6])
    groups = []
    cur = [items[0]]
    for d in items[1:]:
        if d[6] - cur[-1][6] <= _DIAG_DIR_TOL:
            cur.append(d)
        else:
            groups.append(cur)
            cur = [d]
    groups.append(cur)
    if len(groups) > 1 and (groups[0][0][6] + 180.0 -
                            groups[-1][-1][6]) <= _DIAG_DIR_TOL:
        groups[0] = groups[-1] + groups[0]
        groups.pop()
    return groups


def _merge_dir_group(grp):
    u"""同方向簇内贪心配对：垂距<60mm 判重复线（保长弃短），
    60~450mm 且中点落在对方沿线 [0.05,0.95] 判双线墙 → 取中线、厚度取实测。
    """
    wsum = sum(d[5] for d in grp)
    theta = sum(d[6] * d[5] for d in grp) / wsum
    th = math.radians(theta)
    nx, ny = -math.sin(th), math.cos(th)          # 法向
    grp = sorted(grp, key=lambda d: -d[5])        # 按长度降序
    alive = []   # [x1, y1, x2, y2, t_mm, plan]
    for d in grp:
        p, x1, y1, x2, y2, L, ang = d
        mx, my = (x1 + x2) * 0.5, (y1 + y2) * 0.5
        t_src = float(p.get("thickness_mm") or 240.0)
        merged_into = None
        for ai, av in enumerate(alive):
            ax1, ay1, ax2, ay2, at, _ap = av
            dx, dy = ax2 - ax1, ay2 - ay1
            La = math.hypot(dx, dy)
            if La < 1.0:
                continue
            off = abs((mx - (ax1 + ax2) * 0.5) * nx +
                      (my - (ay1 + ay2) * 0.5) * ny)
            pa = ((mx - ax1) * dx + (my - ay1) * dy) / (La * La)
            # 本段被 a 沿向覆盖的比例（相对本段自身长度）。
            # 必须算这个：旧实现只看垂距就判"重复线"，同轴但相隔很远的两面墙
            # 会被整条删掉（合成真值「同轴远隔不得吞墙」实测 8000mm→4000mm），
            # 而 _rechain 只补 <=2600mm 的缝，补不回来。
            tb1 = ((x1 - ax1) * dx + (y1 - ay1) * dy) / (La * La)
            tb2 = ((x2 - ax1) * dx + (y2 - ay1) * dy) / (La * La)
            lo, hi = min(tb1, tb2), max(tb1, tb2)
            cov = max(0.0, min(hi, 1.0) - max(lo, 0.0)) * La / max(L, 1e-9)
            if off < _DIAG_DUP_GAP and cov > _DIAG_DUP_COVER:
                merged_into = ai          # 真重复线：a 更长，本段丢弃
                break
            if (off < max(_DIAG_MERGE_GAP, t_src) and
                    (0.05 <= pa <= 0.95 or cov > _DIAG_DUP_COVER)):
                # 双线墙取中：中线过两中点平均，端点取两者投影范围
                cmx, cmy = (mx + (ax1 + ax2) * 0.5) * 0.5, \
                           (my + (ay1 + ay2) * 0.5) * 0.5
                hx, hy = (ax2 - ax1) * 0.5, (ay2 - ay1) * 0.5
                if L * 0.5 > math.hypot(hx, hy):
                    hx, hy = (x2 - x1) * 0.5, (y2 - y1) * 0.5
                alive[ai] = [cmx - hx, cmy - hy, cmx + hx, cmy + hy,
                             max(off, t_src, at), av[5]]
                merged_into = -2
                break
        if merged_into is None:
            alive.append([x1, y1, x2, y2, t_src, p])
    out = []
    for x1, y1, x2, y2, t, base in alive:
        rec = dict(base)
        g = dict(base.get("geom") or {})
        g["points"] = [[x1, y1], [x2, y2]]
        g["closed"] = False
        g["type"] = "lwpolyline"
        rec["geom"] = g
        rec["thickness_mm"] = int(round(t))
        rec["note"] = ((base.get("note") or u"") +
                       u"；v2.12 同向近带取中").strip(u"； ")
        out.append(rec)
    return out


def consolidate_walls(planned):
    u"""把零散墙 plan 按 (楼层, 层高, 类型) 分组，链成连续墙线后再生成 plan。

    收益：① 构件数大幅下降（性能）；② 单条墙线在 Revit 内自动清理相交；
    ③ 为后续 JoinGeometry 提供干净端点。不改变非墙 plan。纯 Python，可 headless 测。

    v2.8：入口先做「闭合薄轮廓取中」（_outline_centerline_plans），
    双线墙外形还原成中心线单墙；分组键加入 thickness_mm，链出的 plan 带回厚度。
    v2.8b：链化后做墙重叠消解（_resolve_wall_overlaps）——真图崩溃实录驱动。
    """
    wall_plans = []
    others = []
    for p in planned:
        if p["element_type"] == "wall":
            pieces = _outline_centerline_plans(p)
            for q in (pieces if pieces else [p]):
                wall_plans.append(q)
        else:
            others.append(p)
    merged = _build_wall_plans(wall_plans, others)
    # v2.11f: 尾部追加碎片墙再链化（同线共向、间隙<=2600mm 重连，门洞交 Revit 开）
    return _rechain(_diag_dedupe(_resolve_wall_overlaps(merged)))

# --------------------------------------------------------------------------- #
# 4) 轴网归并：精确重复 + 按开间分段的共线轴段 → 一条连续轴网
# --------------------------------------------------------------------------- #
# 真机实测（仙湖别墅.rvt 2026-10-08）：3231 条轴网里只有 1172 条不同定位线，
# 2059 条是精确重复（同一条最多建 6 遍）；剩下 1172 条又多是"按开间逐段画"
# 的轴线（中国施工图习惯：每跨一条，端点接气泡编号），一条真实轴线被拆成几段。
# 后果：轴网气泡糊成一片，且每次重跑再叠一层。
#
# 一个算法同时解决两件事：把同方向的轴段投影到它的方向轴上，按"垂距"分到
# 同一条直线，再对轴向区间做并集。重复段自然合并，共线连续段也自然合并。
# Revit 的 Grid.Create 只接受直线，所以每条合并后的区间发一条两点轴网。
_GRID_ANGLE_TOL_DEG = 1.0    # 同轴方向角容差
_GRID_OFFSET_TOL_MM = 30.0   # 垂距在此范围内算同一条轴线
_GRID_GAP_TOL_MM = 60.0      # 轴向间隙小于此值就接上（气泡端点常有几十 mm 抖动）
_GRID_MIN_LEN_MM = 500.0     # 短于此的轴段丢弃（实测有 248.5mm 的垃圾段）


def _grid_axis_key(seg):
    u"""返回 (角度 mod 180, 方向单位向量, 法向单位向量)。

    seg 的前四项恒为 (x1,y1,x2,y2)；后面还挂着长度与来源 plan，
    所以只取前四项，不要整元解包。
    """
    x1, y1, x2, y2 = seg[0], seg[1], seg[2], seg[3]
    ang = math.degrees(math.atan2(y2 - y1, x2 - x1)) % 180.0
    th = math.radians(ang)
    return ang, (math.cos(th), math.sin(th)), (-math.sin(th), math.cos(th))


def _grid_groups(segs):
    u"""按方向角分桶，桶内再按垂距分直线。返回 [[seg, ...], ...]。"""
    by_ang = sorted(segs, key=lambda s: _grid_axis_key(s)[0])
    buckets = []
    cur = []
    cur_ang = None
    for s in by_ang:
        a = _grid_axis_key(s)[0]
        if cur_ang is None or abs(a - cur_ang) <= _GRID_ANGLE_TOL_DEG:
            cur.append(s)
            cur_ang = a if cur_ang is None else cur_ang
        else:
            buckets.append(cur)
            cur = [s]
            cur_ang = a
    if cur:
        buckets.append(cur)
    # 环绕：0° 与 180° 是同一条线
    if len(buckets) > 1:
        a0 = _grid_axis_key(buckets[0][0])[0]
        a1 = _grid_axis_key(buckets[-1][0])[0]
        if a0 + 180.0 - a1 <= _GRID_ANGLE_TOL_DEG:
            buckets[0] = buckets[-1] + buckets[0]
            buckets.pop()

    lines = []
    for b in buckets:
        if not b:
            continue
        _a, u, n = _grid_axis_key(b[0])
        # 用"中点在法向上的投影"当直线标识，再按同样的一维区间并分组
        offs = []
        for s in b:
            mx, my = (s[0] + s[2]) * 0.5, (s[1] + s[3]) * 0.5
            offs.append((mx * n[0] + my * n[1], s))
        offs.sort(key=lambda t: t[0])
        cur_line = [offs[0][1]]
        cur_off = offs[0][0]
        for o, s in offs[1:]:
            if o - cur_off <= _GRID_OFFSET_TOL_MM:
                cur_line.append(s)
            else:
                lines.append((u, n, cur_line))
                cur_line = [s]
                cur_off = o
        lines.append((u, n, cur_line))
    return lines


def consolidate_grids(planned):
    u"""把零散/重复的轴网段归并成尽量少的连续轴网。非轴网 plan 原样保留。

    **顺序与总数都是契约**：plan_from_json 的返回顺序被 bimconv_build 依赖
    （"results 与 planned 同序"用于逐件补救定位），所以合并结果写回该组第一段
    的原始位置，其余原位删除。只有"短于 _GRID_MIN_LEN_MM 的垃圾段"和
    "连坐标都读不出来的段"是刻意丢弃的，其余一律保留在原位。
    纯 Python，可 headless 测。
    """
    segs = []          # (x1, y1, x2, y2, L, plan, planned_index)
    drop_idx = set()   # 刻意丢弃的原始位置
    for i, p in enumerate(planned):
        if p.get("element_type") != "grid":
            continue
        g = p.get("geom") or {}
        a, b = g.get("start"), g.get("end")
        if not (a and b):
            pts = g.get("points") or []
            if len(pts) >= 2:
                a, b = pts[0], pts[-1]
        if not (a and b):
            continue                      # 读不出坐标：不参与归并，留在原位
        x1, y1 = float(a[0]), float(a[1])
        x2, y2 = float(b[0]), float(b[1])
        L = math.hypot(x2 - x1, y2 - y1)
        if L < _GRID_MIN_LEN_MM:
            drop_idx.add(i)               # 垃圾短段：真机实测有 248.5mm 的
            continue
        segs.append((x1, y1, x2, y2, L, p, i))
    if not segs:
        return [p for i, p in enumerate(planned) if i not in drop_idx]

    replace_idx = {}
    tail = []          # 同一直线拆出的后续段，追加到末尾
    for u, n, line in _grid_groups(segs):
        # 把该直线上所有段投影到方向轴，做区间并
        ivs = []
        for s in line:
            t1 = s[0] * u[0] + s[1] * u[1]
            t2 = s[2] * u[0] + s[3] * u[1]
            ivs.append((min(t1, t2), max(t1, t2), s))
        ivs.sort(key=lambda t: t[0])
        merged = []
        lo, hi, grp = ivs[0]
        grp = [grp]
        for a, b, s in ivs[1:]:
            if a - hi <= _GRID_GAP_TOL_MM:
                hi = max(hi, b)
                grp.append(s)
            else:
                merged.append((lo, hi, grp))
                lo, hi, grp = a, b, [s]
        merged.append((lo, hi, grp))
        # 该组所有原始位置：最小的那个放合并结果，其余删除
        idxs = sorted(s[6] for s in line)
        keep_at = idxs[0]
        for j in idxs[1:]:
            drop_idx.add(j)
        # 法向距离：取组内所有段中点在法向上的均值
        off = sum(((s[0] + s[2]) * 0.5 * n[0] + (s[1] + s[3]) * 0.5 * n[1])
                  for s in line) / max(1, len(line))
        slots = []
        for a, b, grp in merged:
            if (b - a) < _GRID_MIN_LEN_MM:
                for s in grp:
                    drop_idx.add(s[6])
                continue
            slots.append((a, b, grp))
        if not slots:
            drop_idx.add(keep_at)
            continue
        for k, (a, b, grp) in enumerate(slots):
            base = grp[0][5]
            np_ = dict(base)
            g = dict(base.get("geom") or {})
            g["type"] = "line"
            # 投影标量 t 还原成平面点：点 = t*u + off*n
            g["start"] = [a * u[0] + off * n[0], a * u[1] + off * n[1]]
            g["end"] = [b * u[0] + off * n[0], b * u[1] + off * n[1]]
            np_["geom"] = g
            np_["note"] = ((base.get("note") or u"") +
                           u"；轴网归并 %d 段" % len(grp)).strip(u"； ")
            np_["source"] = u";".join((s[5].get("source") or u"") for s in grp[:5])
            if k == 0:
                replace_idx[keep_at] = np_
            else:
                # 同一条直线被切成互不相接的多段：后续段追加到末尾。
                # 不能用 planned 索引当合成键（会和真实索引撞），单独收一份。
                tail.append(np_)
    res = []
    extra = dict(replace_idx)
    for i, p in enumerate(planned):
        if i in extra:
            res.append(extra.pop(i))
            continue
        if i in drop_idx:
            continue
        res.append(p)
    res.extend(extra[k] for k in sorted(extra))
    res.extend(tail)
    return res


# ---- v2.11e → v2.12: 同向近带配对（斜墙 + 正交墙统一处理）----
# 现象（v2.11e 原始动因，5f5e575450 墙堆叠实录）：斜坐标系图 5298 段墙中 3447 对
# 近平行近邻（垂距主峰 100-300mm = 双线墙各自成墙），顶部交叉如筏。
# v2.12 更正（2026-10-07 真图实测）：当时把这条只用在斜墙上、让轴对齐墙"原样透传"，
# 是个**判据漏洞不是性能取舍**。5 张真图（小区住宅楼 270套，ODA 转换）量过：
#   consolidate_walls 把墙段 28288 → 5991（降 78.8%），但**残余的真重复 100% 是
#   轴对齐双线墙**（判据：平行 + 垂距 80~420mm + 轴向重叠>60%），
#   各图残余率 r1 67.7% / r6 60.8% / r5 27.3% / r2 7.3% / r4 5.3%。
#   原因：_resolve_wall_overlaps 只去**同心**重复线（_MERGE_TOL_MM=2mm），
#   _outline_centerline_plans 只管**闭合**轮廓，正交开口双线墙两边都不管。
# 方案：按 (楼层,层高,类型) 分区，区内按方向角(mod 180°)聚簇，簇内贪心配对——
#   垂距 <60mm 判重复线（保长弃短）；60~450mm 且中点落在对方沿线 [0.05,0.95]
#   判双线墙 → 取中线合并，厚度=实测间距。配不上的原样保留。
# 注意：**厚度不进分区键**——实测有 14~39% 的重复对是"一边标了 240、一边没标"，
#   按厚度分组会把它们拆到两个区里，反而合不上。
_DIAG_DIR_TOL = 5.0       # 同簇角度容差（度）
_DIAG_DUP_GAP = 60.0      # 垂距 < 此值 = 重复线
_DIAG_DUP_COVER = 0.5     # 且沿向覆盖 > 此比例才判重复（否则是同轴两面不同墙）
# 双线墙取中的垂距上限。**2026-10-08 按 5 张真图的"最近平行对垂距"分布定的**
# （只统计轴向重叠>60% 的对，20mm 一档）：
#   100-120mm 1654 | 120-140 696 | 180-200 3715 | 200-220 9268(主峰)
#   220-240 293 | 240-260 3075 | [谷 260-280 仅 11] | 280-300 55
#   300-320 518  —— 夹在两个谷之间的独立峰 = 300mm 剪力墙（真图常见厚度）
#   [谷 320-340 仅 19] | 340-400 ~372 | 400-420 207 | 440-720 每档 88~192
#   —— 340 之后是"两面独立墙/墙+柱面/家具"的宽噪声带，没有再成峰的
# 所以界线取 **330**，落在 320-340 这个谷上：收住 300mm 剪力墙，挡掉 340+ 的
# 误并。旧值 450 太松：真机验收时 8 堵 10~16m 的长墙被当成双线并成了
# "DWG-400厚"，那是两个独立构件。
# 注意下面配对时用的是 max(_DIAG_MERGE_GAP, t_src)：已经由闭合轮廓实测出
# 厚度的墙（t_src 可能 >330）允许更宽，那是真厚墙，不该被收紧误伤。
_DIAG_MERGE_GAP = 330.0   # 垂距 < 此值 = 双线墙取中
_DIAG_MIN_LEN = 800.0     # 参与配对的最短线长
_DIAG_OVERLAP_FRAC = 0.5  # 沿向重叠 > 短线长×此系数才配对


def _split_to_segments(merged):
    u"""把墙 plan 拆成单段，每段自带原 plan 的元数据；短段单独返回。

    返回 (segs, passthrough, others)：
      segs        : [plan, x1, y1, x2, y2, L, ang_deg(mod 180)]
      passthrough : 短段（< _DIAG_MIN_LEN）plan，含门洞碎段，不参与配对
      others      : 非墙 plan
    """
    walls = [p for p in merged if p["element_type"] == "wall"]
    others = [p for p in merged if p["element_type"] != "wall"]
    segs = []
    passthrough = []
    for p in walls:
        pts = p["geom"].get("points") or []
        seq = pts + ([pts[0]] if (p["geom"].get("closed") and pts) else [])
        for k in range(len(seq) - 1):
            a, b = seq[k], seq[k + 1]
            q = dict(p)
            g = dict(p["geom"])
            g["points"] = [list(a), list(b)]
            g["closed"] = False
            q["geom"] = g
            x1, y1 = float(a[0]), float(a[1])
            x2, y2 = float(b[0]), float(b[1])
            L = math.hypot(x2 - x1, y2 - y1)
            if L < _DIAG_MIN_LEN:
                passthrough.append(q)   # 短段不参与，原样保留
                continue
            ang = math.degrees(math.atan2(y2 - y1, x2 - x1)) % 180.0
            segs.append([q, x1, y1, x2, y2, L, ang])
    return segs, passthrough, others


def _diag_dedupe(merged):
    u"""同向近带配对：重复线去重 + 双线墙取中（斜墙与正交墙一视同仁）。"""
    segs, passthrough, others = _split_to_segments(merged)
    if not segs:
        return merged

    # 先按 (楼层, 层高, 类型提示) 分区，再在区内做方向聚簇。
    # 分区是必须的：旧实现让整簇共用 grp[0] 的 level/height/type_hint，
    # 斜墙时代影响面小，一旦纳入正交墙，整张图所有横墙会被压成同一楼层。
    buckets = {}
    for d in segs:
        p = d[0]
        key = (p.get("level"), p.get("height"), p.get("type_hint"))
        buckets.setdefault(key, []).append(d)

    out_plans = list(passthrough)
    for items in buckets.values():
        for grp in _angle_groups(items):
            if len(grp) < 2:
                out_plans.extend(d[0] for d in grp)
                continue
            out_plans.extend(_merge_dir_group(grp))
    return out_plans + others


# ---- v2.11f: 碎片墙再链化（城投主楼平面门窗寄宿实录驱动）----
# 现象：门编码就在门旁，但宿主墙段长仅 200~621mm（< 门宽 600~1500mm），
# Revit 开洞必失败（17/21 编码门被卡）。根因：v2.8b 裁角把共线墙切成碎片；
# 且 CAD 图习惯在门洞处断开墙线（画门符号），洞两侧本是同一面墙。
# 方案：按（方向簇 × 同线垂距）分组，沿向间隙 <= 2600mm（覆盖门洞 900~2100）
# 的共线段重连成整墙——洞口由 Revit 门窗寄宿时再切开，几何无损。
_RECHAIN_GAP = 2600.0      # 沿向间隙连接阈值（mm），≈最大门宽+余量
_RECHAIN_OFF_TOL = 30.0    # 同线垂距容差（mm）
_RECHAIN_DIR_BIN = 2.5     # 斜线方向角分箱（度），复用 v2.11e 语义


def _seg_dclass(x1, y1, x2, y2):
    u"""线段方向分类：'H' / 'V' / ('D', bin)。H/V 用 10mm 宽容（手绘抖动）。"""
    dx, dy = abs(x2 - x1), abs(y2 - y1)
    if dy <= 10.0:
        return "H"
    if dx <= 10.0:
        return "V"
    ang = math.degrees(math.atan2(y2 - y1, x2 - x1)) % 180.0
    return ("D", int(round(ang / _RECHAIN_DIR_BIN)) % 72)


def _rechain(merged):
    u"""共线碎片墙再链化。仅连接同方向、同线（垂距<=30mm）、间隙<=2600mm 的段。

    纯 Python，可 headless 测。斜墙与轴对齐墙统一处理（方向簇内再按垂距分线）。
    只增不变：没连上任何邻居的段原样保留（plan 元数据无损）。
    """
    walls = [p for p in merged if p["element_type"] == "wall"]
    others = [p for p in merged if p["element_type"] != "wall"]
    segs = []   # [plan, x1,y1,x2,y2, L, dclass, parent_idx]
    pidx = {}   # id(plan) -> parent_idx
    for p in walls:
        if id(p) not in pidx:
            pidx[id(p)] = len(pidx)
        pts = p["geom"].get("points") or []
        seq = pts + ([pts[0]] if (p["geom"].get("closed") and pts) else [])
        for k in range(len(seq) - 1):
            a, b = seq[k], seq[k + 1]
            x1, y1 = float(a[0]), float(a[1])
            x2, y2 = float(b[0]), float(b[1])
            L = math.hypot(x2 - x1, y2 - y1)
            if L < 1.0:
                continue
            segs.append([p, x1, y1, x2, y2, L,
                         _seg_dclass(x1, y1, x2, y2), pidx[id(p)]])
    if not segs:
        return merged

    # 分组：楼层/层高/类型/厚度(缺省按 240，与 _resolve 同口径)/方向
    # v2.11f b: 厚度缺省归一——改派墙(无厚度)与规则墙(t=240)本是同一面墙，
    # 精确分组会把门洞两侧拆进不同组而无法重连
    groups = {}
    for s in segs:
        p = s[0]
        key = (p.get("level", ""), p.get("height", 0),
               p.get("type_hint", ""),
               float(p.get("thickness_mm") or 240.0),
               s[6])
        groups.setdefault(key, []).append(s)

    out = list(others)
    n_joined = 0
    consumed = set()
    for key, grp in groups.items():
        # 参考线 = 组内最长段
        ref = max(grp, key=lambda s: s[5])
        rx1, ry1, rx2, ry2 = ref[1], ref[2], ref[3], ref[4]
        Lr = math.hypot(rx2 - rx1, ry2 - ry1)
        if Lr < 1.0:
            for s in grp:
                out.append(s[0])
            continue
        ux, uy = (rx2 - rx1) / Lr, (ry2 - ry1) / Lr
        nx, ny = -uy, ux
        rmx, rmy = (rx1 + rx2) * 0.5, (ry1 + ry2) * 0.5
        # 每段 → (pmin, pmax, off, seg)
        proj = []
        for s in grp:
            _p, x1, y1, x2, y2, L, _dc, _pi = s
            p0 = (x1 - rx1) * ux + (y1 - ry1) * uy
            p1 = (x2 - rx1) * ux + (y2 - ry1) * uy
            proj.append([min(p0, p1), max(p0, p1), 0.0, s])
        # 同线垂距（带符号，用于分桶）
        for rec in proj:
            s = rec[3]
            _p, x1, y1, x2, y2, L, _dc, _pi = s
            rec[2] = ((x1 + x2) * 0.5 - rmx) * nx + \
                     ((y1 + y2) * 0.5 - rmy) * ny
        lines = {}
        for rec in proj:
            b = int(round(rec[2] / _RECHAIN_OFF_TOL))
            lines.setdefault(b, []).append(rec)
        for _b, recs in lines.items():
            recs.sort(key=lambda r: r[0])
            i = 0
            while i < len(recs):
                cur = recs[i]
                j = i + 1
                pmax = cur[1]
                chain_n = 1
                while j < len(recs):
                    if recs[j][0] - pmax <= _RECHAIN_GAP:
                        pmax = max(pmax, recs[j][1])
                        chain_n += 1
                        j += 1
                    else:
                        break
                if chain_n >= 2:
                    # 重连：端点投回参考线（off 用桶中心）
                    bx = _b * _RECHAIN_OFF_TOL
                    ax_ = rx1 + ux * cur[0] + nx * bx
                    ay_ = ry1 + uy * cur[0] + ny * bx
                    bx_ = rx1 + ux * pmax + nx * bx
                    by_ = ry1 + uy * pmax + ny * bx
                    base = max((r[3] for r in recs[i:j]),
                               key=lambda s: s[5])[0]
                    rec2 = {
                        "element_type": "wall",
                        "reliability": base.get("reliability", "mid"),
                        "level": base.get("level", ""),
                        "height": base.get("height", 3000),
                        "type_hint": base.get("type_hint", ""),
                        "level_basis": base.get("level_basis") or u"",
                        "source": base.get("source", ""),
                        "geom": {"type": "lwpolyline",
                                 "points": [[ax_, ay_], [bx_, by_]],
                                 "closed": False,
                                 "layer": base["geom"].get("layer", "")},
                        "note": u"v2.11f 再链化 x%d" % chain_n,
                        "thickness_mm": int(round(key[3]))
                        if key[3] else base.get("thickness_mm"),
                    }
                    out.append(rec2)
                    n_joined += chain_n
                    for r in recs[i:j]:
                        consumed.add(id(r[3]))
                    i = j
                else:
                    i += 1
    # 未被链化的处理：
    #   - plan 的所有段都没连上 → 原样保留（多段折线不被拆散）
    #   - plan 有段被连上 → 未连段逐段保留（ unavoidably 拆散，带原元数据）
    consumed_parents = set()
    for s in segs:
        if id(s) in consumed:
            consumed_parents.add(s[7])
    emitted = set()
    for s in segs:
        if id(s) in consumed:
            continue
        pi = s[7]
        if pi not in consumed_parents:
            # 整 plan 未动 → 原 plan 保留一次
            if pi not in emitted:
                emitted.add(pi)
                out.append(s[0])
        else:
            # plan 被部分链化 → 未连段以单段 plan 保留（带原元数据）
            p = s[0]
            rec = {
                "element_type": "wall",
                "reliability": p.get("reliability", "mid"),
                "level": p.get("level", ""),
                "height": p.get("height", 3000),
                "type_hint": p.get("type_hint", ""),
                "source": p.get("source", ""),
                "geom": {"type": "lwpolyline",
                         "points": [[s[1], s[2]], [s[3], s[4]]],
                         "closed": False,
                         "layer": p["geom"].get("layer", "")},
                "note": p.get("note"),
                "thickness_mm": p.get("thickness_mm"),
            }
            out.append(rec)
    return out


# --------------------------------------------------------------------------- #
# 2) 墙段接合（最重几何运算，需谨慎）
# --------------------------------------------------------------------------- #
def _join_walls(doc, walls, max_joins=2000):
    u"""把共享端点的墙用 JoinGeometryUtils 接合，返回成功接合处数。

    注意：重合/近重合墙（两端点一致）接合会触发 Revit 原生崩溃，已在循环内跳过；
    共线对（直墙延续）也会跳过（JoinGeometry 只会报"无法使图元保持连接"）。
    max_joins 限制接合总数，防止超大图纸拖垮性能。
    """
    tol = 1.0 * MM_TO_FT  # ~1mm

    def key(xyz):
        return (int(round(xyz.X / tol)), int(round(xyz.Y / tol)),
                int(round(xyz.Z / tol)))

    def _near(a, b):
        u"""两端点是否近重合（用于跳过重合墙，避免 Join 原生崩溃）。"""
        try:
            return a.DistanceTo(b) < tol * 2
        except Exception:
            return False

    def _collinear(ci, cj):
        u"""两墙方向是否共线（夹角 < ~2°，含反向）。共线对 JoinGeometry
        要么失败要么毫无意义，是接合风暴的引爆点，必须跳过。"""
        try:
            d1 = ci.GetEndPoint(1) - ci.GetEndPoint(0)
            d2 = cj.GetEndPoint(1) - cj.GetEndPoint(0)
            l1 = d1.GetLength()
            l2 = d2.GetLength()
            if l1 < tol or l2 < tol:
                return True
            cross = abs(d1.X * d2.Y - d1.Y * d2.X) / (l1 * l2)
            return cross < 0.035
        except Exception:
            return False

    buck = {}
    for wll in walls:
        try:
            c = wll.Location.Curve
            eps = [c.GetEndPoint(0), c.GetEndPoint(1)]
        except Exception:
            continue
        for p in eps:
            buck.setdefault(key(p), []).append(wll)
    joined = 0
    for _, ws in buck.items():
        for i in range(len(ws)):
            for j in range(i + 1, len(ws)):
                try:
                    ci = ws[i].Location.Curve
                    cj = ws[j].Location.Curve
                    ei0, ei1 = ci.GetEndPoint(0), ci.GetEndPoint(1)
                    ej0, ej1 = cj.GetEndPoint(0), cj.GetEndPoint(1)
                    if (_near(ei0, ej0) and _near(ei1, ej1)) or \
                       (_near(ei0, ej1) and _near(ei1, ej0)):
                        continue
                    if _collinear(ci, cj):
                        continue
                    try:
                        DB.WallUtils.AllowWallJoinAtEnd(ws[i], 0)
                        DB.WallUtils.AllowWallJoinAtEnd(ws[i], 1)
                        DB.WallUtils.AllowWallJoinAtEnd(ws[j], 0)
                        DB.WallUtils.AllowWallJoinAtEnd(ws[j], 1)
                    except Exception:
                        pass
                    DB.JoinGeometryUtils.JoinGeometry(doc, ws[i], ws[j])
                    joined += 1
                    if joined >= max_joins:
                        return joined
                except Exception:
                    pass
    return joined


# --------------------------------------------------------------------------- #
# 4) 墙链环路 → 自动楼板（V2.4a：结构图没有板边线时的兜底出板）
# --------------------------------------------------------------------------- #
def _snap_pt(p, tol=5.0):
    u"""端点吸附网格键（5mm）。"""
    return (int(round(float(p[0]) / tol)), int(round(float(p[1]) / tol)))


def _poly_area(pts):
    u"""鞋带公式面积（mm²），取绝对值；点列不须回收起点。"""
    a = 0.0
    n = len(pts)
    for i in range(n):
        x1, y1 = pts[i][0], pts[i][1]
        x2, y2 = pts[(i + 1) % n][0], pts[(i + 1) % n][1]
        a += x1 * y2 - x2 * y1
    return abs(a) / 2.0


def slab_plans_from_wall_loops(planned, min_area_mm2=30e6, max_area_mm2=3e9,
                               gap_mm=6000.0):
    u"""无楼板边线图纸的自动出板：墙链环路 / 墙簇外包 两级兜底。

    实测本类结构图墙线碎成 0.3m 级小段且充满缺口（门洞/断线），
    单链闭合、端点追环、平面图元提取全部失效（水密环路不存在），
    最终可靠的是：把墙段按「端点间距 <= gap(6m)」并查集聚类成建筑簇，
    每簇外包矩形即楼板（建筑轮廓视觉上就是矩形；L 形楼会多覆盖缺口，
    属已知近似）。有水密环路的图纸走精确环路，其余走簇矩形。
    面积过滤默认 30 ~ 3000 平米。
    """
    walls = [p for p in planned if p["element_type"] == "wall"]
    if not walls:
        return []
    paths = []
    levels = []
    for p in walls:
        pts = p["geom"].get("points") or []
        if len(pts) >= 2:
            paths.append([(float(q[0]), float(q[1])) for q in pts])
            levels.append(p.get("level") or u"1F")
    if not paths:
        return []

    out = []

    # ---- 第一级：跨链追环（水密图纸出精确轮廓）----
    adj = {}
    self_closed = [False] * len(paths)
    for i, pts in enumerate(paths):
        ka, kb = _snap_pt(pts[0]), _snap_pt(pts[-1])
        if ka == kb:
            self_closed[i] = True
            continue
        adj.setdefault(ka, []).append((i, 0))
        adj.setdefault(kb, []).append((i, 1))
    used = [False] * len(paths)
    loops = []
    for i, pts in enumerate(paths):
        if self_closed[i]:
            used[i] = True
            loops.append((list(pts), levels[i]))
    for start_i, pts in enumerate(paths):
        if used[start_i] or self_closed[start_i]:
            continue
        chain = [list(q) for q in pts]
        start_node = _snap_pt(pts[0])
        cur_node = _snap_pt(pts[-1])
        used[start_i] = True
        touched = [start_i]
        cur_level = levels[start_i]
        ok = True
        guard = 0
        while cur_node != start_node:
            guard += 1
            if guard > len(paths) + 8:
                ok = False
                break
            nxt = None
            for (j, endn) in adj.get(cur_node, []):
                if not used[j] and not self_closed[j]:
                    nxt = (j, endn)
                    break
            if nxt is None:
                ok = False
                break
            j, endn = nxt
            used[j] = True
            touched.append(j)
            pj = paths[j]
            seg = [list(q) for q in (pj[::-1] if endn == 0 else pj)]
            chain.extend(seg)
            cur_node = _snap_pt(seg[-1])
        if not ok:
            for j in touched:
                used[j] = False
            continue
        if cur_node == start_node and len(chain) >= 4:
            loops.append((chain, cur_level))

    def _emit(pts, lvl, tag):
        clean = [pts[0]]
        for q in pts[1:]:
            if abs(q[0] - clean[-1][0]) > 1e-6 or abs(q[1] - clean[-1][1]) > 1e-6:
                clean.append(q)
        if len(clean) < 3:
            return False
        if abs(clean[0][0] - clean[-1][0]) > 1e-6 or abs(clean[0][1] - clean[-1][1]) > 1e-6:
            clean.append(list(clean[0]))
        area = _poly_area(clean)
        if area < min_area_mm2 or area > max_area_mm2:
            return False
        out.append({
            "element_type": "slab",
            "reliability": "experimental",
            "level": lvl,
            "height": 0,
            "type_hint": "",
            "source": tag,
            "geom": {"type": "lwpolyline",
                     "points": [[q[0], q[1]] for q in clean],
                     "closed": True,
                     "layer": ""},
            "note": u"楼板（%s，%.0f 平米）" % (tag, area / 1e6),
        })
        return True

    for pts, lvl in loops:
        _emit(pts, lvl, u"墙链环路")

    # ---- 第二级：墙簇外包矩形（碎线图纸的兜底）----
    segs = []
    seg_lvl = []
    for i, pts in enumerate(paths):
        if used[i]:
            continue
        for k in range(len(pts) - 1):
            a, b = pts[k], pts[k + 1]
            if abs(a[0] - b[0]) < 1.0 and abs(a[1] - b[1]) < 1.0:
                continue
            segs.append((a, b))
            seg_lvl.append(levels[i])
    if not segs:
        return out

    parent = list(range(len(segs)))

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb

    cell = GAP = gap_mm
    buckets = defaultdict(list)

    def ck(p):
        return (int(p[0] // cell), int(p[1] // cell))

    for i, (a, b) in enumerate(segs):
        for c in {ck(a), ck(b)}:
            buckets[c].append(i)
    for c, lst in buckets.items():
        n = len(lst)
        for x in range(n):
            for y in range(x + 1, n):
                i, j = lst[x], lst[y]
                if find(i) == find(j):
                    continue
                a1, b1 = segs[i]
                a2, b2 = segs[j]
                done = False
                for p1 in (a1, b1):
                    for p2 in (a2, b2):
                        if (abs(p1[0] - p2[0]) <= gap_mm
                                and abs(p1[1] - p2[1]) <= gap_mm):
                            union(i, j)
                            done = True
                            break
                    if done:
                        break

    groups = defaultdict(list)
    for i in range(len(segs)):
        groups[find(i)].append(i)
    for g, idxs in groups.items():
        xs, ys = [], []
        lvl_count = defaultdict(int)
        for i in idxs:
            (x1, y1), (x2, y2) = segs[i]
            xs.extend([x1, x2])
            ys.extend([y1, y2])
            lvl_count[seg_lvl[i]] += 1
        w, h = max(xs) - min(xs), max(ys) - min(ys)
        area = w * h
        if area < min_area_mm2 or area > max_area_mm2:
            continue
        lvl = sorted(lvl_count.items(), key=lambda kv: -kv[1])[0][0]
        poly = [[min(xs), min(ys)], [max(xs), min(ys)],
                [max(xs), max(ys)], [min(xs), max(ys)], [min(xs), min(ys)]]
        _emit(poly, lvl, u"墙簇外包")
    return out


# --------------------------------------------------------------------------- #
# 3) 门/窗寄宿索引：按端点/中点分桶，就近找宿主墙
# --------------------------------------------------------------------------- #
def _build_wall_index(walls_with_curves):
    u"""把墙曲线按端点/中点分桶，供门/窗就近寄宿快速查找。返回 (buckets, key函数)。"""
    tol = 2000.0 * MM_TO_FT  # 2m 桶

    def key(xyz):
        return (int(round(xyz.X / tol)), int(round(xyz.Y / tol)))

    buckets = {}
    for w, c in walls_with_curves:
        try:
            p0 = c.GetEndPoint(0)
            p1 = c.GetEndPoint(1)
        except Exception:
            continue
        mid = DB.XYZ((p0.X + p1.X) / 2.0, (p0.Y + p1.Y) / 2.0, (p0.Z + p1.Z) / 2.0)
        for p in (p0, mid, p1):
            buckets.setdefault(key(p), []).append((w, c))
    return buckets, key


def _nearest_wall(pt, buckets, key):
    u"""在索引里找距 pt 最近的墙，返回 (墙, 墙曲线, 距离)。找不到返回 (None, None, 1e9)。"""
    bx, by = key(pt)
    cands = []
    for dx in (-1, 0, 1):
        for dy in (-1, 0, 1):
            cands.extend(buckets.get((bx + dx, by + dy), []))
    seen = set()
    uniq = []
    for w, c in cands:
        wid = w.Id.IntegerValue
        if wid not in seen:
            seen.add(wid)
            uniq.append((w, c))
    if not uniq:  # 邻桶无候选则全扫兜底
        for vs in buckets.values():
            for w, c in vs:
                wid = w.Id.IntegerValue
                if wid not in seen:
                    seen.add(wid)
                    uniq.append((w, c))
    best = None
    best_c = None
    best_d = 1e9
    for w, c in uniq:
        try:
            d = c.Distance(pt)
        except Exception:
            continue
        if d < best_d:
            best_d = d
            best = w
            best_c = c
        if best_d < 1e-6:
            break
    return best, best_c, best_d


# ---------------------------------------------------------------------------
# 5) 子图聚类：一张 DWG 里常常并排画着十几张图（各层平面 + 立面 + 剖面 + 详图）。
#    而翻模的归位单位一直是「文件」，于是这些图被当成一张平面图整体建进同一层。
#    2026-10-11 高层住宅「平立面图.dwg」实测：19789 条墙线在图面上分成 30 个
#    空间连通簇（24 簇 ≥100 条，覆盖 98.5%），计划里却只有一个楼层可落
#    → 必然「墙重叠」。这里先只做如实计数，供预览揭示。
# ---------------------------------------------------------------------------
_SUBDRAWING_CELL_MM = 10000.0   # 连通网格边长：远大于墙线抖动，远小于图与图的间距
_SUBDRAWING_MIN_ITEMS = 100     # 低于此的簇按噪声算（节点详图、指北针、文字底线）


def subdrawing_clusters(planned, cell_mm=_SUBDRAWING_CELL_MM):
    u"""按图面位置把计划连通成分张子图 → [(下标...), ...]，按规模降序。

    只看坐标、不看图层名：立面/剖面图上画的墙线用的就是和平面一样的 WALL 层名，
    图层区分不了它们，能区分的只有"它们画在图纸的哪一块"。
    """
    cells = {}
    for i, p in enumerate(planned):
        if not p.get("geom"):
            continue
        try:
            segs = _wall_segments(p)
        except Exception:
            continue
        for a, b in segs:
            for pt in (a, b):
                try:
                    key = (int(pt[0] // cell_mm), int(pt[1] // cell_mm))
                except Exception:
                    continue
                bucket = cells.get(key)
                if bucket is None:
                    cells[key] = [i]
                elif bucket[-1] != i:
                    bucket.append(i)

    seen = set()
    out = []
    for key in cells:
        if key in seen:
            continue
        seen.add(key)
        stack = [key]
        idxs = []
        while stack:
            k = stack.pop()
            idxs.extend(cells[k])
            for dx in (-1, 0, 1):
                for dy in (-1, 0, 1):
                    n = (k[0] + dx, k[1] + dy)
                    if n in cells and n not in seen:
                        seen.add(n)
                        stack.append(n)
        out.append(sorted(set(idxs)))
    out.sort(key=len, reverse=True)
    return out


def subdrawing_summary(planned):
    u"""(子图簇数, 簇内图元数, 总图元数)，噪声簇不计入簇数。"""
    cl = subdrawing_clusters(planned)
    big = [c for c in cl if len(c) >= _SUBDRAWING_MIN_ITEMS]
    return len(big), sum(len(c) for c in big), len(planned)
