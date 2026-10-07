# -*- coding: utf-8 -*-
"""精修计算器：从 05_plan.json 墙段几何反推洞口真实宽度，产出精修载荷。

CAD 画法：门窗处墙线断开，缺口 = 洞口。计算逻辑：
  1) consolidate 后的墙段按轴线（H/V）+偏移分组（同一条墙线上的段）
  2) 门窗点投影到墙线轴 → 若点落在某段跨度内=墙上开门(无缺口)，否则=缺口开门
  3) 缺口宽 = 左右相邻端头的轴向距离（图纸真实洞宽）
  4) 产出：delete_ids(建歪的) + create_walls(填缺口) + create_door_window(重放)
"""
import json
import sys

sys.path.insert(0, r"E:/bim-toolkit/pyrevit-bim-panel/BIMToolkit.extension/lib")
import bimconv_rules as rules
import bimconv_walls as cw

PLAN = r"D:/CAD/CAD/建筑图纸/房屋设计图/小区住宅楼建筑结构施工图 270套/北馨住宅平立面图/05_plan.json"
CSV = (r"E:/bim-toolkit/pyrevit-bim-panel/BIMToolkit.extension/BIMToolkit.tab/"
       u"BIM 工具.panel/DWG翻模.pushbutton/mapping_rules.csv")
LEVEL = u"标高 1"
EPS = 5.0        # 轴向吸附 mm
PERP_TOL = 250.0  # 点到墙线的垂直容差 mm
DEFAULT_W = {"door": 900.0, "window": 1200.0}
HEIGHT = {"door": 2100.0, "window": 1500.0}
SILL = {"door": 0.0, "window": 900.0}


def seg_of(wall):
    pts = wall["geom"].get("points") or []
    if len(pts) < 2:
        return None
    (x0, y0), (x1, y1) = pts[0][:2], pts[1][:2]
    if abs(y1 - y0) <= 1.0 and abs(x1 - x0) > 1.0:
        axis, off = "H", y0
    elif abs(x1 - x0) <= 1.0:
        axis, off = "V", x0
    else:
        return None  # 斜墙不参与缺口分析
    a0, a1 = (x0, x1) if axis == "H" else (y0, y1)
    return axis, off, min(a0, a1), max(a0, a1)


def main():
    pj = json.load(open(PLAN, encoding="utf-8-sig"))
    csv = rules.load_rules(CSV)
    planned, unmatched = rules.plan_from_json(pj, csv, [LEVEL])
    planned = cw.consolidate_walls(planned)
    walls = [p for p in planned if p["element_type"] == "wall"]
    openers = [p for p in planned if p["element_type"] in ("door", "window")]

    segs = []
    for w in walls:
        s = seg_of(w)
        if s:
            segs.append((s, w))
    print("墙段 %d（可分析 %d），门窗 %d" % (len(walls), len(segs), len(openers)))

    delete_ids = []          # 由主流程填（模型实例 id）
    fillers, placements = [], []
    report = []
    n_gap = n_onwall = n_far = 0

    for p in openers:
        kind = p["element_type"]
        pt = (p["geom"].get("point") or [0, 0])[:2]
        px, py = float(pt[0]), float(pt[1])
        if abs(px) < 1.0 and abs(py) < 1.0:
            n_far += 1
            report.append((kind, pt, "无定位点(窗线),跳过", None, None))
            continue
        best = None  # (axis, off, dist_perp)
        for (axis, off, a0, a1), w in segs:
            perp = abs(py - off) if axis == "H" else abs(px - off)
            lo, hi = (px, py) if axis == "H" else (py, px)
            inside = a0 - EPS <= lo <= hi <= a1 + EPS
            axial_gap = max(a0 - lo, lo - a1, 0.0)
            d = perp + axial_gap * 0.1
            if perp <= PERP_TOL and (best is None or d < best[2]):
                best = (axis, off, d, inside)
        if best is None:
            n_far += 1
            report.append((kind, pt, "远离任何墙线", DEFAULT_W[kind], None))
            continue
        axis, off, _, inside = best
        # 同轴所有段（同方向同偏移）
        grp = [((a0, a1), w) for ((ax, of, a0, a1), w) in segs
               if ax == axis and abs(of - off) <= 60.0]
        s_pt = px if axis == "H" else py
        # 段内？(点在某一跨度的实体墙上)
        on_wall = any(a0 - EPS <= s_pt <= a1 + EPS for (a0, a1), _ in grp)
        if on_wall:
            # 墙上开门：无缺口，宽度用默认，位置就在点上是已有的实例
            n_onwall += 1
            report.append((kind, pt, "实体墙上(已建,重摆放)", DEFAULT_W[kind], None))
            placements.append({
                "kind": kind, "point": [px, py],
                "width_mm": DEFAULT_W[kind], "height_mm": HEIGHT[kind],
                "sill_mm": SILL[kind], "level": LEVEL,
                "_mode": "recreate-on-wall",
            })
            continue
        # 缺口：左右端头
        lefts = [a1 for (a0, a1), _ in grp if a1 <= s_pt + EPS]
        rights = [a0 for (a0, a1), _ in grp if a0 >= s_pt - EPS]
        if not lefts or not rights:
            # v2.8c 剔了短垛 → 缺口单侧无边界：从段端朝点方向按默认宽造洞
            if lefts:
                edge, direction = max(lefts), +1.0
            else:
                edge, direction = min(rights), -1.0
            gap = DEFAULT_W[kind]
            la, rb = (edge, edge + gap * direction)
            if la > rb:
                la, rb = rb, la
            mode = "单侧推断 %.0f" % gap
        else:
            la, rb = max(lefts), min(rights)
            gap = rb - la
            mode = "缺口 %.0f" % gap
        if gap < 350 or gap > 3600:
            n_far += 1
            report.append((kind, pt, "缺口宽 %.0f 越界" % gap, DEFAULT_W[kind], None))
            continue
        n_gap += 1
        thick = None
        for (a0, a1), w in grp:
            if abs(a1 - la) <= EPS or abs(a0 - rb) <= EPS:
                t = w.get("thickness_mm")
                h = w.get("height") or 3000.0
                thick = (t or 240.0, h)
        t_mm, h_mm = thick if thick else (240.0, 3000.0)
        mid = (la + rb) / 2.0
        pt_gap = [mid, off] if axis == "H" else [off, mid]
        # 填墙（缺口本身）
        w_start = [la, off] if axis == "H" else [off, la]
        w_end = [rb, off] if axis == "H" else [off, rb]
        fillers.append({"start": w_start, "end": w_end,
                        "height_mm": h_mm, "thickness_mm": t_mm,
                        "level": LEVEL, "_for": "%s@%.0f" % (kind, mid)})
        placements.append({
            "kind": kind, "point": pt_gap,
            "width_mm": gap, "height_mm": HEIGHT[kind],
            "sill_mm": SILL[kind], "level": LEVEL,
            "_mode": mode,
        })
        report.append((kind, pt, mode, gap, (w_start, w_end)))

    print("缺口开门 %d | 实体墙重放 %d | 异常 %d" % (n_gap, n_onwall, n_far))
    out = {
        "level": LEVEL,
        "delete_ids": delete_ids,  # 执行前由模型查询填充
        "create_walls": fillers,
        "create_door_window": placements,
    }
    with open(r"E:\bim-toolkit\mcp\refine_payload.json", "wb") as f:
        f.write(b"\xef\xbb\xbf")
        f.write(json.dumps(out, ensure_ascii=False, indent=2).encode("utf-8"))
    print("已写 E:/bim-toolkit/mcp/refine_payload.json")
    # 摘要
    from collections import Counter
    print("洞宽分布:", sorted(round(p["width_mm"]) for p in placements))


if __name__ == "__main__":
    main()
