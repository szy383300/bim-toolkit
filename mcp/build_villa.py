# -*- coding: utf-8 -*-
u"""四层小型别墅一键建模（Revit 开 + MCP Bridge 已点击后运行）。

用法:  python build_villa.py
产出:  E:\\bim-toolkit\\models\\四层别墅.rvt

布局(mm, 原点在西南角):
  外包 12000x9000, 外墙 240, 内墙 100, 层高 3000x4 + 坡屋顶
  L1: 玄关/楼梯间/卫生间(西条) + 客厅/卧室/餐厅/厨房
  L2/L3: 卫生间2/楼梯间/卫生间(西条) + 主卧/卧室/书房/走廊 + 南阳台
  L4: 楼梯间/卫生间(西条) + 大活动室 + 北室/卫生间
  楼梯: 双跑, 梯段宽1200, 起点(1800,3300) 北上
  屋顶: 双坡30°, 檐口挑出600, 屋脊沿X
"""
import io
import json
import os
import sys

sys.path.insert(0, r"E:\bim-toolkit\mcp")
from bridge_client import call  # noqa: E402

SAVE_PATH = r"E:\bim-toolkit\models\四层别墅.rvt"
EXT_T, INT_T = 240, 100
H = 3000

# v7.10.4 重跑前清场：删除旧模型的全部实例（墙/门窗/楼板/屋顶/楼梯/栏杆/
# 线脚），跳过 ElementType 类型 —— 类型删了后面建墙就没有墙类型可用了。
WIPE_CODE = u"""# -*- coding: utf-8 -*-
cats = ["OST_Walls", "OST_Doors", "OST_Windows", "OST_Floors",
        "OST_Roofs", "OST_Stairs", "OST_StairsRuns", "OST_StairsLandings",
        "OST_Railings", "OST_Lines"]
ids = []
skipped_types = 0
for cname in cats:
    try:
        bic = BuiltInCategory.LookupOption(cname)
    except Exception:
        continue
    if bic is None:
        continue
    for e in FilteredElementCollector(doc).OfCategory(bic).ToElements():
        try:
            if isinstance(e, ElementType):
                skipped_types += 1
                continue
            ids.append(e.Id)
        except Exception:
            pass
deleted = 0
for i in ids:
    try:
        doc.Delete(i)
        deleted += 1
    except Exception:
        pass
_result = {"found": len(ids), "deleted": deleted,
           "skipped_types": skipped_types}
"""

LVL = [u"标高 1", u"标高 2", u"标高 3", u"标高 4", u"屋顶"]
ELEV = [0, 3000, 6000, 9000, 12000]


def ext_walls(lvl):
    return [
        {"start": [0, 0], "end": [12000, 0]},
        {"start": [0, 9000], "end": [12000, 9000]},
        {"start": [0, 0], "end": [0, 9000]},
        {"start": [12000, 0], "end": [12000, 9000]},
    ]


def west_walls(lvl):
    u"""西条: 卫生间/楼梯间/卫生间2 (所有层共用)"""
    return [
        {"start": [2400, 0], "end": [2400, 9000]},
        {"start": [0, 3000], "end": [2400, 3000]},
        {"start": [0, 6900], "end": [2400, 6900]},
    ]


def l1_walls():
    return west_walls(0) + [
        {"start": [8400, 0], "end": [8400, 9000]},
        {"start": [2400, 5200], "end": [8400, 5200]},
        {"start": [8400, 4500], "end": [12000, 4500]},
    ]


def l2_walls():
    return west_walls(0) + [
        {"start": [7400, 0], "end": [7400, 9000]},
        {"start": [2400, 4500], "end": [12000, 4500]},
        {"start": [7400, 2700], "end": [12000, 2700]},
    ]


def l4_walls():
    return west_walls(0) + [
        {"start": [7400, 0], "end": [7400, 9000]},
        {"start": [7400, 2700], "end": [12000, 2700]},
    ]


def walls_batch(walls, lvl, thickness):
    out = []
    for w in walls:
        out.append({"start": w["start"], "end": w["end"],
                    "height_mm": H, "thickness_mm": thickness,
                    "level": lvl})
    return out


def slab(fp, lvl, structural=False):
    return {"points": fp, "level": lvl, "structural": structural}


FP = [[0, 0], [12000, 0], [12000, 9000], [0, 9000]]
BAL = [[4200, 0], [9000, 0], [9000, -1500], [4200, -1500]]


def dw(kind, x, y, w, level, h=None, sill=None):
    o = {"kind": kind, "point": [x, y], "width_mm": w,
         "height_mm": h or (2100 if kind == "door" else 1500),
         "level": level}
    if sill:
        o["sill_mm"] = sill
    return o


def openings():
    ops = []
    # ---- L1 ----
    ops += [dw("door", 1200, 0, 1200, LVL[0])]          # 入户门(南墙)
    ops += [dw("window", 5400, 0, 1800, LVL[0], sill=900)]
    ops += [dw("window", 10200, 0, 1500, LVL[0], sill=900)]
    ops += [dw("window", 10200, 9000, 1500, LVL[0], sill=900)]
    ops += [dw("window", 5400, 9000, 1500, LVL[0], sill=900)]
    ops += [dw("window", 0, 7800, 600, LVL[0], sill=1500)]
    ops += [dw("door", 2400, 1500, 900, LVL[0])]        # 玄关->客厅
    ops += [dw("door", 2400, 4800, 900, LVL[0])]        # 客厅->楼梯间
    ops += [dw("door", 2400, 7800, 900, LVL[0])]        # 卧室->卫生间
    ops += [dw("door", 5400, 5200, 900, LVL[0])]        # 客厅->卧室
    ops += [dw("door", 8400, 2200, 900, LVL[0])]        # 客厅->餐厅
    ops += [dw("door", 10200, 4500, 900, LVL[0])]       # 餐厅->厨房
    # ---- L2 / L3 ----
    for lvl in (LVL[1], LVL[2]):
        ops += [dw("door", 6600, 0, 1800, lvl)]         # 卧室->阳台(推拉)
        ops += [dw("window", 3600, 0, 1500, lvl, sill=900)]
        ops += [dw("window", 9700, 0, 600, lvl, sill=1500)]
        ops += [dw("window", 4900, 9000, 1500, lvl, sill=900)]
        ops += [dw("window", 9700, 9000, 1500, lvl, sill=900)]
        ops += [dw("window", 12000, 6700, 1500, lvl, sill=900)]
        ops += [dw("window", 0, 7800, 600, lvl, sill=1500)]
        ops += [dw("door", 2400, 4800, 900, lvl)]       # 楼梯间->主卧
        ops += [dw("door", 2400, 3800, 900, lvl)]       # 楼梯间->卧室
        ops += [dw("door", 1200, 6900, 900, lvl)]       # 楼梯间->卫生间
        ops += [dw("door", 1200, 3000, 900, lvl)]       # 卫生间2->楼梯间
        ops += [dw("door", 7400, 3600, 900, lvl)]       # 卧室->走廊
        ops += [dw("door", 9700, 4500, 900, lvl)]       # 走廊->书房
        ops += [dw("door", 9700, 2700, 900, lvl)]       # 走廊->卫生间
    # ---- L4 ----
    ops += [dw("window", 4900, 0, 1800, LVL[3], sill=900)]
    ops += [dw("window", 9700, 0, 1500, LVL[3], sill=900)]
    ops += [dw("window", 4900, 9000, 1800, LVL[3], sill=900)]
    ops += [dw("window", 9700, 9000, 1500, LVL[3], sill=900)]
    ops += [dw("window", 12000, 6700, 1500, LVL[3], sill=900)]
    ops += [dw("window", 0, 7800, 600, LVL[3], sill=1500)]
    ops += [dw("door", 2400, 4800, 900, LVL[3])]        # 楼梯间->活动室
    ops += [dw("door", 7400, 1350, 900, LVL[3])]        # 活动室->卫生间
    ops += [dw("door", 7400, 5000, 900, LVL[3])]        # 活动室->北室
    return ops


MATS = [  # (用途, 材质关键词列表, 应用对象)
    (u"外墙", [u"砖", u"砌块", u"混凝土"], "ext"),
    (u"内墙", [u"涂料", u"石膏", u"混凝土"], "int"),
    (u"屋面", [u"瓦", u"沥青", u"混凝土"], "roof"),
    (u"阳台", [u"木", u"混凝土"], "balcony"),
]


def pick_material(avail, keywords):
    for kw in keywords:
        for a in avail:
            if kw in a:
                return a
    return None


def main():
    # 0. 体检
    r = call({"type": "ping", "id": 1}, timeout=15)
    print(u"[0] 桥 %s 文档 %s" % (r.get("result", {}).get("version"),
                                 r.get("result", {}).get("document")))

    # 0.5 清场：删掉上一次运行的残留实例，避免重复建模
    r = call({"type": "execute_code", "id": 14, "code": WIPE_CODE},
             timeout=280)
    res = r.get("result", r)
    print(u"[0.5] 清场: %s" % json.dumps(res, ensure_ascii=False)[:200])

    # 1. 标高
    r = call({"type": "create_levels", "id": 2,
              "levels": [{"name": n, "elevation_mm": e}
                         for n, e in zip(LVL, ELEV)]}, timeout=120)
    print(u"[1] 标高:", json.dumps(r.get("result", r), ensure_ascii=False)[:200])

    # 2. 墙 —— 外墙/内墙分批(材质分组需要)
    ext_ids, int_ids = [], []
    for lvl in LVL[:4]:
        r = call({"type": "create_walls", "id": 3,
                  "walls": walls_batch(ext_walls(lvl), lvl, EXT_T)},
                 timeout=280)
        res = r.get("result", r)
        ext_ids += res.get("ids") or []
        print(u"[2] %s 外墙: %s" % (lvl, res.get("created")))
    batches = [(l1_walls(), LVL[0]), (l2_walls(), LVL[1]),
               (l2_walls(), LVL[2]), (l4_walls(), LVL[3])]
    for walls, lvl in batches:
        r = call({"type": "create_walls", "id": 4,
                  "walls": walls_batch(walls, lvl, INT_T)}, timeout=280)
        res = r.get("result", r)
        int_ids += res.get("ids") or []
        print(u"[2] %s 内墙: %s" % (lvl, res.get("created")))

    # 3. 楼板
    floors = [slab(FP, LVL[0], True), slab(FP, LVL[1]),
              slab(FP, LVL[2]), slab(FP, LVL[3]),
              slab(BAL, LVL[1]), slab(BAL, LVL[2])]
    r = call({"type": "create_floors", "id": 5, "floors": floors},
             timeout=280)
    res = r.get("result", r)
    fl_ids = res.get("ids") or []
    bal_ids = fl_ids[-2:] if len(fl_ids) >= 6 else []
    print(u"[3] 楼板: created=%s failed=%s" % (res.get("created"),
                                               res.get("failed")))

    # 4. 楼梯 x3
    for k in range(3):
        r = call({"type": "create_stairs", "id": 6,
                  "base_level": LVL[k], "top_level": LVL[k + 1],
                  "start": [1800, 3300], "dir": [0, 1],
                  "width_mm": 1200, "total_rise_mm": 3000,
                  "run_mm": 2100}, timeout=280)
        res = r.get("result", r)
        print(u"[4] 楼梯 %s->%s: %s %s" % (
            LVL[k], LVL[k + 1], res.get("created"),
            res.get("fallback") or u"真实楼梯"))

    # 5. 屋顶
    r = call({"type": "create_roof_gable", "id": 7,
              "x1": -600, "y1": -600, "x2": 12600, "y2": 9600,
              "level": LVL[4], "slope_deg": 30, "ridge_axis": "x"},
             timeout=280)
    res = r.get("result", r)
    roof_id = res.get("id")
    print(u"[5] 屋顶: %s" % json.dumps(res, ensure_ascii=False)[:200])

    # 6. 栏杆
    rail_path = [[4200, 0], [4200, -1500], [9000, -1500], [9000, 0]]
    for lvl in (LVL[1], LVL[2]):
        r = call({"type": "create_railing", "id": 8, "path": rail_path,
                  "level": lvl}, timeout=280)
        res = r.get("result", r)
        print(u"[6] 栏杆 %s: %s" % (lvl, json.dumps(
            res, ensure_ascii=False)[:150]))

    # 7. 门窗
    ops = openings()
    r = call({"type": "create_door_window", "id": 9, "openings": ops},
             timeout=280)
    res = r.get("result", r)
    print(u"[7] 门窗: created=%s failed=%s" % (res.get("created"),
                                               res.get("failed")))
    for e in (res.get("errors") or [])[:10]:
        print(u"   - %s" % e[:120])

    # 8. 材质（先探可用材质表）
    probe = call({"type": "set_material", "id": 10, "name": "___probe___"},
                 timeout=120)
    avail = []
    err = probe.get("result", {}).get("error", "")
    if u"available" in err:
        avail = [s.strip() for s in
                 err.split(u"available:")[-1].split(u",") if s.strip()]
    print(u"[8] 可用材质: %s" % u", ".join(avail)[:260])

    def apply(mat_kws, ids, label):
        if not ids:
            return
        m = pick_material(avail, mat_kws)
        if not m:
            print(u"[8] %s: 未找到合适材质" % label)
            return
        r = call({"type": "set_material", "id": 11, "name": m,
                  "ids": ids}, timeout=280)
        res = r.get("result", r)
        print(u"[8] %s <- %s: assigned=%s" % (label, m,
                                              res.get("assigned")))

    apply([u"砖", u"砌块", u"混凝土"], ext_ids, u"外墙")
    apply([u"涂料", u"石膏", u"混凝土"], int_ids, u"内墙")
    if roof_id:
        apply([u"瓦", u"沥青", u"混凝土"], [roof_id], u"屋面")
    if bal_ids:
        apply([u"木", u"混凝土"], bal_ids, u"阳台")

    # 9. 保存（doc 已 SaveAs 过则直接 Save 覆盖；勿 os.remove —— 文件被
    #    Revit 锁定且沙箱安全删除会拦截）
    os.makedirs(os.path.dirname(SAVE_PATH), exist_ok=True)
    r = call({"type": "save_document", "id": 12, "path": SAVE_PATH},
             timeout=280)
    res = r.get("result", r)
    print(u"[9] 保存: %s" % json.dumps(res, ensure_ascii=False)[:200])

    # 10. 验收
    counts = {}
    for cat in (u"门", u"窗", u"墙", u"楼板", u"屋顶", u"楼梯", u"栏杆"):
        r = call({"type": "get_elements", "id": 13, "category": cat,
                  "limit": 300}, timeout=60)
        counts[cat] = len(r.get("result", {}).get("elements", []))
    print(u"[10] 验收: " + u"  ".join(
        u"%s=%s" % (k, v) for k, v in counts.items()))
    print(u"完成 -> %s" % SAVE_PATH)


if __name__ == "__main__":
    main()
