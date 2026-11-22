# -*- coding: utf-8 -*-
"""plan_eval.py —— 翻模「分类保真度」的合成真值评测。

为什么是合成真值：人工翻的基准模型基本没了，而"覆盖率"这种指标要打折看
（未匹配里大量是植物/家具/图签，本来就不建）。所以反过来做：按已知规格
生成 DXF，真值就是我们自己写进去的东西，再用 handle 做连接键精确算
precision / recall —— 计划项带 `source: "line#<handle>"`，两边能一一对上。

场景是按实测校准的，不是想象：
  * 6/6 张真实施工图 $INSUNITS=0（未声明单位）→ units_zero
  * 语料里真有 1 张 $INSUNITS=6 = **米** 图        → units_meters
  * 英尺/英寸是同一根因的另一头（本仓库暂无样本）  → units_inch
  * 真实图纸 98% 图元在具名图层，但确有 LAYER3 这类 → nameless
  * 弧类实体占 0.6~4%，以 ARC/CIRCLE 独立实体出现   → arcs
  * 块插入里 25~68% 带属性（门窗尺寸来源）          → attribs

**范围要说清楚**：本工具量的是 DWG→DXF→JSON→plan 的**分类**保真度，
不是"翻出来的楼像不像"。双线墙聚类成单墙那一段属于 v2。

用法：
    python tools/plan_eval.py                 # 打印报告
    python tools/plan_eval.py --json out.json # 另存机器可读结果
    python tools/plan_eval.py --gate          # 回归闸门（见 _gate 说明）

需要 ezdxf（bim-dev venv 里有）。不需要 Revit。
"""
from __future__ import division
import argparse
import json
import math
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
LIB = os.path.join(REPO, "pyrevit-bim-panel", "BIMToolkit.extension", "lib")
RULES = os.path.join(
    REPO, "pyrevit-bim-panel", "BIMToolkit.extension", "BIMToolkit.tab",
    u"BIM 工具.panel", "DWG翻模.pushbutton", "mapping_rules.csv")
sys.path.insert(0, LIB)

import ezdxf                                          # noqa: E402
import dwg_to_json as dj                              # noqa: E402
import bimconv_rules as br                            # noqa: E402

# $INSUNITS 取值表**抄自 ezdxf.units 的权威常量**（IN=1 FT=2 MM=4 CM=5 M=6），
# 不是凭印象：这一版初稿把 M 和 IN 写成 1 和 6，正好反了，
# 连带把语料里那张 INSUNITS=6 的图误判成"英寸图"（其实是米图）。
INSUNITS_NONE, INSUNITS_IN, INSUNITS_FT = 0, 1, 2
INSUNITS_MM, INSUNITS_CM, INSUNITS_M = 4, 5, 6


def _add(layer, maker, *a, **kw):
    """加一个图元并记下它的 handle。"""
    kw.setdefault("dxfattribs", {})["layer"] = layer
    e = maker(*a, **kw)
    return e.dxf.handle


def build_rect_walls(d, lw, la, lc, ln, scale=1.0):
    """一个 6000×4000 的双线外墙矩形 + 轴线 + 柱块 + 窗线 + 门块。

    返回 {handle: element_type} 真值表与期望墙中线总长(mm, 已按 scale 折算)。
    """
    truth = {}
    s = scale

    def rect(x0, y0, x1, y1, layer):
        pts = [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]
        hs = []
        for i in range(4):
            a, b = pts[i], pts[(i + 1) % 4]
            hs.append(_add(layer, d.modelspace().add_line,
                           (a[0] * s, a[1] * s, 0), (b[0] * s, b[1] * s, 0)))
        return hs

    # 外圈 6000x4000，内圈偏移 240 —— 双线墙的典型画法
    for h in rect(0, 0, 6000, 4000, lw) + rect(240, 240, 5760, 3760, lw):
        truth[h] = "wall"
    # 期望墙中线总长：中线矩形 (6000-120)x(4000-120) 的周长
    exp_len = 2 * ((6000 - 120) + (4000 - 120))

    for x in (0, 3000, 6000):
        truth[_add(la, d.modelspace().add_line,
                   (x * s, -500 * s, 0), (x * s, 4500 * s, 0))] = "grid"
    # 窗：**块插入**，不是裸线。真图实测（r4/r2/r5）"窗"里 85~95% 是
    # WINDOW 层上的玻璃线/框线/对角示意线，真窗是块。以前这个场景把窗画成
    # 4 条短线，等于拿我的简化当现实——守卫把裸线排除后召回率归零，
    # 错的是场景不是守卫。
    wblk = d.blocks.new(name="WIN-900")
    wblk.add_line((0, 0), (900, 0))
    wblk.add_line((0, 120), (900, 120))     # 玻璃线，无开启弧 -> 仍是窗
    for y in (0, 4000):
        for x in (1500, 3500):
            wi = d.modelspace().add_blockref("WIN-900",
                                             insert=(x * s, y * s, 0),
                                             dxfattribs={"layer": ln})
            truth[wi.dxf.handle] = "window"
    # 窗洞上的细节线：必须被排除，不能各建一个窗
    for y in (0, 4000):
        truth[_add(ln, d.modelspace().add_line,
                   (1600 * s, (y + 60) * s, 0),
                   (2300 * s, (y + 60) * s, 0))] = "__detail__"
    blk = d.blocks.new(name="COL-400")
    blk.add_circle((0, 0), 200)
    for x, y in ((600, 600), (5400, 600), (600, 3400), (5400, 3400)):
        ins = d.modelspace().add_blockref("COL-400", insert=(x * s, y * s, 0))
        truth[ins.dxf.handle] = "column"
    dref = d.blocks.new(name="DOOR-900")
    dref.add_line((0, 0), (900, 0))
    dref.add_arc((0, 0), 900, 0, 90)   # 开启弧 → v2.9 靠它判门
    dm = d.modelspace().add_blockref("DOOR-900", insert=(2500 * s, 0, 0))
    truth[dm.dxf.handle] = "door"
    return truth, exp_len


def scenario(name, insunits, scale=1.0, layers=None, arcs=False, attribs=False):
    """造一张合成图，返回 (dxf 路径, 真值表, 期望墙中线总长)。"""
    lw, la, lc, ln = (layers or (u"WALL", u"AXIS", u"COLUMN", u"WINDOW"))
    d = ezdxf.new("R2010")
    d.header["$INSUNITS"] = insunits
    for l in (lw, la, lc, ln, u"ROOF", u"DOTE"):
        if l not in d.layers:
            d.layers.add(l)
    truth, exp_len = build_rect_walls(d, lw, la, lc, ln, scale=scale)

    if arcs:
        # 弧墙与圆柱：真实图里弧是以 ARC/CIRCLE 独立实体出现的（bulge 计数为 0）
        m = d.modelspace()
        h = _add(lw, m.add_arc, (3000 * scale, 2000 * scale, 0), 1500 * scale, 0, 180)
        truth[h] = "wall"
        exp_len += math.pi * 1500        # 期望值恒为"真实毫米数"，不随 scale 变
        h = _add(lc, m.add_circle, (1000 * scale, 2000 * scale, 0), 250 * scale)
        truth[h] = "column"

    if attribs:
        # 真图里 25~68% 的块插入带属性，天正块的 base_point 常偏离原点。
        # 更要命的是"块名说门、图层说窗"这种冲突（05.dwg 实测有）：insert 本身
        # 按块名判成门，但 v2.11 把它炸开出来的内线继承了 WINDOW 层 → 又判成窗，
        # 同一个实物会被建两遍。这里把插入放在 WINDOW 层就是为了把这条打出来。
        b = d.blocks.new(name="DOOR-W")
        b.base_point = (0, -100)
        b.add_line((0, -100), (1000, -100))
        b.add_attdef(u"宽度")
        b.add_attdef(u"高度")
        ref = d.modelspace().add_blockref("DOOR-W",
                                          insert=(4500 * scale, 2000 * scale, 0),
                                          dxfattribs={"layer": "WINDOW",
                                                      "rotation": 0,
                                                      "xscale": 1.5, "yscale": 1.5})
        try:
            ref.add_auto_attribs({u"宽度": "1000", u"高度": "2100"})
        except Exception:
            pass
        truth[ref.dxf.handle] = "door"

    # 屋面层与点划线层：验证 exclude / DOTE→grid 两条新规则
    m = d.modelspace()
    for y in (5000, 5200):
        truth[_add(u"ROOF", m.add_line, (0, y * scale, 0),
                   (6000 * scale, y * scale, 0))] = "__exclude__"
    truth[_add(u"DOTE", m.add_line, (-800 * scale, 0, 0),
               (-800 * scale, 4000 * scale, 0))] = "grid"

    path = os.path.join(tempfile.gettempdir(), "planev_%s.dxf" % name)
    d.saveas(path)
    return path, truth, exp_len


TYPES = ("wall", "grid", "column", "window", "door")

# 对照组：只考"图层名/块名 → 类型"这条规则链本身，单位与图层名都有语义。
# 这里任何一项退化，都说明规则或 reader 被改坏了，闸门必须红。
CONTROL = ("baseline_mm", "units_zero")

# 已知缺陷台账。闸门**放过**这些，但每轮都打印出来；修好了就删条目，
# 删不掉的条目就是欠着的技术债——比"分数看着挺好"诚实。
KNOWN_DEFECTS = [
    dict(scenario="units_meters", type="wall", metric="recall", value=0.0,
         why=u"声明为米的图：全部 LINE 被『碎线<50mm』滤掉，墙/轴/窗召回 0。"
             u"根因 _MIN_LINE_MM 与下游尺寸都不看 $INSUNITS"),
    dict(scenario="units_inch", type=None, metric="wall_len_err_pct", value=96.2,
         why=u"英寸图五项召回全 100%，但墙线只有 749mm/期望 19520mm —— "
             u"几何短 25 倍。这条是'召回率单独看会骗人'的活证据。"
             u"（原先记的是 window/recall=0：窗改成块插入后躲过长度过滤，"
             u"那是**掩盖不是修好**，根因 _MIN_LINE_MM 不看 $INSUNITS 仍在）"),
    dict(scenario="arcs", type="wall", metric="recall", value=8.0 / 9.0,
         why=u"ARC 不进 entities → 弧墙丢（真实语料弧类占 0.6~4%）"),
    dict(scenario="arcs", type="column", metric="recall", value=0.8,
         why=u"同上：CIRCLE 画的圆柱丢"),
    dict(scenario="nameless_layers", type="wall", metric="recall", value=0.0,
         why=u"图层名无语义（LAYER3）时墙 0 命中——规则引擎的能力边界，不是 bug"),
    # block_attribs/window/precision 0.80 -> 1.00：已真修好，故删除该条。
    # 修因不是调参，而是「门窗-细节线」守卫顺带把块炸出的内线挡掉了 ——
    # DOOR-W 块的内线继承 WINDOW 层，以前既按块名建成门、又按层建成窗。
]


def _metric(r, t, metric):
    if t is None:                      # 顶层指标（如墙线长度误差）
        return r.get(metric)
    return (r.get("types") or {}).get(t, {}).get(metric)


def evaluate(name, **kw):
    path, truth, exp_len = scenario(name, **kw)
    try:
        _, _, entities, sa, ss = dj.read_dwg(path)
    except Exception as e:
        # 故意不删：链路崩了的时候，那张合成图就是复现件
        return {"scenario": name, "fatal": "%s: %s" % (type(e).__name__, str(e)[:160]),
                "kept_dxf": path, "entities": 0}
    rules = br.load_rules(RULES)
    planned, unmatched = br.plan_from_json({"entities": entities}, rules,
                                           [u"标高 1", u"标高 2"])
    got = {}
    for p in planned:
        src = p.get("source") or ""
        h = src.split("#")[-1] if "#" in src else ""
        got[h] = p.get("element_type")
    excl = set((u.get("handle") or "") for u in unmatched if u.get("excluded"))
    res = {"scenario": name, "entities": len(entities),
           "filtered_annotation": sa, "filtered_short": ss,
           "types": {}}
    for t in TYPES:
        exp = [h for h, v in truth.items() if v == t]
        tp = sum(1 for h in exp if got.get(h) == t)
        fp = sum(1 for h, v in got.items() if v == t and truth.get(h) != t)
        fn = len(exp) - tp
        prec = tp / float(tp + fp) if (tp + fp) else (1.0 if not exp else 0.0)
        rec = tp / float(len(exp)) if exp else 1.0
        res["types"][t] = {"expected": len(exp), "planned": tp + fp, "tp": tp,
                           "precision": round(prec, 3), "recall": round(rec, 3)}
    # 明确不建：屋面图元应进 excluded 而不是"未覆盖"
    exp_ex = [h for h, v in truth.items() if v == "__exclude__"]
    res["excluded_expected"] = len(exp_ex)
    res["excluded_found"] = sum(1 for h in exp_ex if h in excl)
    res["duplicated_from_block"] = sum(
        1 for p in planned
        if (p.get("geom") or {}).get("exploded")
        and (p.get("source") or "").split("#")[-1] not in truth)
    # 窗层细节线（玻璃线/框线）必须被排除：真图实测"窗"里 85~95% 是这种线，
    # 不排掉就会给每个窗多建三四个假窗。
    exp_det = [h for h, v in truth.items() if v == "__detail__"]
    res["detail_expected"] = len(exp_det)
    res["detail_excluded"] = sum(
        1 for h in exp_det
        if h not in got and h in excl)
    # 墙中线长度保真度（双线取半的粗估；只统计 LINE 实体，read_dwg 不输出 ARC）
    wl = 0.0
    for e in entities:
        if got.get(e.get("handle")) != "wall":
            continue
        if e.get("type") == "line" and e.get("start") and e.get("end"):
            wl += math.hypot(e["end"][0] - e["start"][0], e["end"][1] - e["start"][1])
        if e.get("type") == "lwpolyline":
            pts = e.get("points") or []
            for i in range(len(pts) - 1):
                wl += math.hypot(pts[i + 1][0] - pts[i][0], pts[i + 1][1] - pts[i][1])
    res["wall_len_expected_mm"] = round(exp_len, 1)
    res["wall_len_planned_mm"] = round(wl / 2.0, 1)
    res["wall_len_err_pct"] = (round(abs(wl / 2.0 - exp_len) * 100.0 / exp_len, 1)
                               if exp_len else None)
    try:
        os.remove(path)
    except OSError:
        pass
    return res


SCENARIOS = [
    ("baseline_mm", dict(insunits=INSUNITS_MM)),
    ("units_zero", dict(insunits=INSUNITS_NONE)),          # 6/6 真图就是这样
    ("units_meters", dict(insunits=INSUNITS_M, scale=0.001)),
    ("units_inch", dict(insunits=INSUNITS_IN, scale=1 / 25.4)),
    ("arcs", dict(insunits=INSUNITS_MM, arcs=True)),
    ("nameless_layers", dict(insunits=INSUNITS_MM,
                             layers=(u"LAYER3", u"LAYER1", u"L2", u"L4"))),
    ("block_attribs", dict(insunits=INSUNITS_MM, attribs=True)),
]


def _wall_plan(pts, level=u"标高 1", thk=None, h=3000.0):
    return {"element_type": "wall", "reliability": "mature", "level": level,
            "height": h, "type_hint": u"基本墙", "source": "synth",
            "thickness_mm": thk, "note": u"",
            "geom": {"type": "lwpolyline", "layer": u"WALL",
                     "points": [[float(x), float(y)] for x, y in pts],
                     "closed": False}}


def _segs(plans):
    out = []
    for p in plans:
        pts = p["geom"]["points"]
        for i in range(len(pts) - 1):
            a, b = pts[i], pts[i + 1]
            out.append((a[0], a[1], b[0], b[1], math.hypot(b[0] - a[0], b[1] - a[1])))
    return out


def eval_wall_merge():
    u"""consolidate_walls 的几何真值测试（纯 Python，不需要 DXF/Revit）。

    为什么单开一节：分类保真度只回答"这条线算不算墙"，不回答
    "两面墙有没有被并成一面 / 一面墙有没有被吃掉"。后者才是翻模观感的核心，
    而且这个模块有过 965/973 墙湮灭的事故记录，必须有真值卡着。
    """
    import bimconv_walls as bw
    cases = []

    def chk(name, plans, want_n, want_len=None, want_thk=None, note=u""):
        got = [p for p in bw.consolidate_walls(list(plans))
               if p["element_type"] == "wall"]
        n = len(got)
        glen = sum(s[4] for s in _segs(got))
        ok = (n == want_n)
        if want_len is not None and abs(glen - want_len) > want_len * 0.06:
            ok = False
        thks = set(p.get("thickness_mm") for p in got if p.get("thickness_mm"))
        if want_thk is not None and want_thk not in thks:
            ok = False
        cases.append({"name": name, "ok": ok, "want_n": want_n, "got_n": n,
                      "got_len": round(glen, 0),
                      "want_len": round(want_len, 0) if want_len else None,
                      "thks": sorted(t for t in thks if t is not None),
                      "note": note})

    # 1) 标准双线墙：两条平行 6000 长线，间距 240 → 必须并成 1 面中线墙
    chk(u"双线墙取中", [_wall_plan([(0, 0), (6000, 0)]),
                        _wall_plan([(0, 240), (6000, 240)])],
        1, 6000, 240, u"真图 100% 残余重复就是这种")
    # 2) 单线墙不该被动
    chk(u"单线墙保持", [_wall_plan([(0, 0), (6000, 0)])], 1, 6000)
    # 3) 同轴但相隔 5000：是两面不相干的墙，一段都不能丢。
    #    旧实现里 off<60 的"重复线"分支不检查轴向投影，会把短的那条整条删掉，
    #    而 _rechain 只补 <=2600 的缝 —— 这条就是卡这个洞的。
    chk(u"同轴远隔不得吞墙", [_wall_plan([(0, 0), (4000, 0)]),
                              _wall_plan([(9000, 0), (13000, 0)])],
        2, 8000, note=u"间隙 5000 > _rechain 的 2600，两段都必须活着")
    # 4) 门洞断开（间隙 1000）：_rechain 设计上会把 ≤2600 的共线缝重连成整墙
    #    （门洞交给 Revit 寄宿门窗时再切），所以期望 1 面、总长 6000（含洞口）。
    chk(u"门洞断墙不吞长度", [_wall_plan([(0, 0), (2000, 0)]),
                              _wall_plan([(3000, 0), (6000, 0)])],
        1, 6000, note=u"重连成整墙 0..6000，洞口由 Revit 再切开")
    # 5) 垂直相交：不同方向簇，不能互并
    chk(u"正交墙不互并", [_wall_plan([(0, 0), (6000, 0)]),
                          _wall_plan([(3000, -1000), (3000, 1000)])],
        2, 8000)
    # 6) 平行但相隔 3000（两个房间各自的墙）：不能当成双线并掉
    chk(u"远距平行墙保持", [_wall_plan([(0, 0), (6000, 0)]),
                            _wall_plan([(0, 3000), (6000, 3000)])],
        2, 12000)
    # 6b) 相隔 400mm 的两面独立长墙：真机验收实锤过的误并（8 堵 10~16m 竖墙
    #     被并成 "DWG-400厚"）。_DIAG_MERGE_GAP 按真图垂距分布从 450 收到 300。
    chk(u"400mm间距不得当双线并", [_wall_plan([(0, 0), (12000, 0)]),
                                    _wall_plan([(0, 400), (12000, 400)])],
        2, 24000, note=u"界线落在真图垂距分布 320-340 的谷上，340+ 是噪声带")
    # 7) 元数据分区：同样几何放在两个楼层，必须各留各的（旧实现整簇共用
    #    grp[0] 的 level，纳入正交墙后会把全图横墙压成一个楼层）
    got = [p for p in bw.consolidate_walls([
        _wall_plan([(0, 0), (6000, 0)], level=u"标高 1"),
        _wall_plan([(0, 240), (6000, 240)], level=u"标高 1"),
        _wall_plan([(0, 0), (6000, 0)], level=u"标高 2"),
        _wall_plan([(0, 240), (6000, 240)], level=u"标高 2")])
        if p["element_type"] == "wall"]
    lv = sorted(set(p["level"] for p in got))
    cases.append({"name": u"跨楼层不得串元数据", "ok": (lv == [u"标高 1", u"标高 2"]
                                                   and len(got) == 2),
                  "want_n": 2, "got_n": len(got), "got_len": None,
                  "want_len": None, "thks": [], "levels": lv,
                  "note": u"每层各并各的，两层结果不能塌成一层"})
    return cases


def eval_grid_merge():
    u"""consolidate_grids 的几何真值测试（纯 Python，不需要 DXF/Revit）。

    为什么要有：真机量到 3231 条轴网只有 1172 条不同定位线（64% 精确重复），
    归并算法一旦把两条不同的轴线误接成一条，丢的是整条轴网——这种错在预览里
    只表现为"数字变小了"，看着像好事。所以必须有真值钉住"该合的合、不该合的不合"。
    """
    import bimconv_walls as bw

    def gp(x1, y1, x2, y2, level=u"标高 1"):
        return {"element_type": "grid", "reliability": "mature", "level": level,
                "height": 0.0, "type_hint": u"", "source": "synth", "note": u"",
                "geom": {"type": "line", "layer": u"AXIS",
                         "start": [float(x1), float(y1)],
                         "end": [float(x2), float(y2)]}}

    def run(plans):
        got = [p for p in bw.consolidate_grids(list(plans))
               if p["element_type"] == "grid"]
        segs = []
        for p in got:
            a, b = p["geom"]["start"], p["geom"]["end"]
            segs.append((a[0], a[1], b[0], b[1],
                         (a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2))
        return got, segs

    cases = []

    def chk(name, plans, want_n, want_len=None, note=u""):
        got, segs = run(plans)
        ok = len(got) == want_n
        tot = sum(s[4] ** 0.5 for s in segs)
        if want_len is not None and abs(tot - want_len) > max(1.0, want_len * 0.02):
            ok = False
        cases.append({"name": name, "ok": ok, "want_n": want_n, "got_n": len(got),
                      "got_len": round(tot, 0),
                      "want_len": round(want_len, 0) if want_len else None,
                      "note": note})

    # 1) 完全相同的三条线 → 一条
    chk(u"精确重复去重", [gp(0, 0, 12000, 0)] * 3, 1, 12000,
        u"真机 64% 的轴网是这种")
    # 2) 按开间逐段画的共线轴段 → 接成一条 12m 轴线
    chk(u"开间分段接成连续轴", [gp(0, 0, 4000, 0), gp(4000, 0, 8000, 0),
                                 gp(8000, 0, 12000, 0)], 1, 12000,
        u"施工图标准画法")
    # 3) 端点有 20mm 抖动也要接上（气泡端点常不精确）
    chk(u"端点抖动仍可接续", [gp(0, 0, 4000, 0), gp(4020, 15, 8000, 0)], 1,
        8000, note=u"间隙 20mm < 60mm 容差")
    # 4) 两条相隔 3000 的平行轴线：绝不能合并
    chk(u"不同轴线不得误接", [gp(0, 0, 12000, 0), gp(0, 3000, 12000, 3000)], 2,
        24000, note=u"误接会整条轴网消失")
    # 5) 垂直两条 → 各自一条
    chk(u"正交轴网不互并", [gp(0, 0, 12000, 0), gp(6000, -1000, 6000, 13000)], 2,
        12000 + 14000)
    # 6) 短于 500mm 的垃圾轴段丢掉（真机实测有 248.5mm 的）
    chk(u"垃圾短段丢弃", [gp(0, 0, 12000, 0), gp(20000, 0, 20248, 0)], 1, 12000,
        note=u"248mm 段应被丢")
    # 7) 同一条线上相隔很远的两段（两栋楼共线）：保持两条
    chk(u"共线远隔不硬接", [gp(0, 0, 4000, 0), gp(20000, 0, 24000, 0)], 2, 8000,
        note=u"间隙 16m >> 60mm 容差")
    # 8) 非轴网 plan 必须原样穿过
    mixed = [gp(0, 0, 12000, 0), {"element_type": "wall", "level": u"标高 1",
                                  "height": 3000.0, "type_hint": u"", "source": "w",
                                  "note": u"", "geom": {"type": "line", "layer": u"WALL",
                                                        "start": [0, 0], "end": [1, 0]}}]
    got = bw.consolidate_grids(mixed)
    cases.append({"name": u"非轴网原样保留", "ok": (len(got) == 2),
                  "want_n": 2, "got_n": len(got), "got_len": None,
                  "want_len": None, "note": u"墙 plan 不能被吃掉"})
    return cases

def eval_column_merge():
    u"""_consolidate_columns 的真值测试：并成一根、带上截面、且**一根都不能丢**。

    最后这条是重点——这个函数第一版把"既不是插入点也不是闭合轮廓"的柱 plan
    静默丢掉了，r4 实测柱 plan 从 5248 掉到 99。吞构件比不合并严重得多，
    所以除了合并结果，还要断言总数守恒。
    """
    import bimconv_rules as br

    def ins(x, y, handle="A"):
        return {"element_type": "column", "reliability": "mature", "level": u"标高 1",
                "height": 3000.0, "type_hint": u"", "source": "insert#%s" % handle,
                "note": u"", "geom": {"type": "insert", "layer": u"COLUMN",
                                      "handle": handle, "block": u"_FZH",
                                      "point": [float(x), float(y), 0.0]}}

    def rect(cx, cy, w, d, handle="B"):
        x0, y0, x1, y1 = cx - w / 2.0, cy - d / 2.0, cx + w / 2.0, cy + d / 2.0
        return {"element_type": "column", "reliability": "experimental",
                "level": u"标高 1", "height": 3000.0, "type_hint": u"",
                "source": "lwpolyline#%s" % handle, "note": u"",
                "geom": {"type": "lwpolyline", "layer": u"COLUMN", "handle": handle,
                         "closed": True, "exploded": True, "src_block": u"_FZH",
                         "points": [[x0, y0, 0.0], [x1, y0, 0.0],
                                    [x1, y1, 0.0], [x0, y1, 0.0]]}}

    def line(x1, y1, x2, y2, handle="L"):
        return {"element_type": "column", "reliability": "experimental",
                "level": u"标高 1", "height": 3000.0, "type_hint": u"",
                "source": "line#%s" % handle, "note": u"",
                "geom": {"type": "line", "layer": u"COLUMN", "handle": handle,
                         "start": [float(x1), float(y1)], "end": [float(x2), float(y2)]}}

    cases = []

    def chk(name, plans, want_n, want_sized=None, note=u""):
        got = [p for p in br._consolidate_columns(list(plans))
               if p["element_type"] == "column"]
        ok = len(got) == want_n
        if want_sized is not None:
            ok = ok and sum(1 for p in got if p.get("width_mm")) == want_sized
        cases.append({"name": name, "ok": ok, "want_n": want_n, "got_n": len(got),
                      "want_len": want_sized,
                      "got_len": sum(1 for p in got if p.get("width_mm")),
                      "note": note})

    # 1) 插入点 + 它自己的 240x240 轮廓 → 一根柱，且带截面
    chk(u"标记与轮廓并成一根", [ins(0, 0), rect(0, 0, 240, 240)], 1, 1,
        u"r2/r6 实测每根柱都这样双份")
    # 2) 三根柱各带轮廓 → 三根，不是六根
    chk(u"多根柱各自配对",
        [ins(0, 0, "1"), rect(0, 0, 400, 400, "2"),
         ins(5000, 0, "3"), rect(5000, 0, 300, 500, "4"),
         ins(9000, 0, "5"), rect(9000, 0, 300, 300, "6")], 3, 3)
    # 3) 只有插入点、没有轮廓 → 保留（尺寸未知），不能丢
    chk(u"无轮廓的插入点保留", [ins(0, 0)], 1, 0,
        u"尺寸未知要如实留着，不是扔掉")
    # 4) 线段柱必须原样穿过 —— 第一版就是在这里静默吞掉了 r4 的 5000 多根
    chk(u"线段柱不得被吞", [line(0, 0, 300, 0), line(1000, 0, 1300, 0)], 2, 0,
        u"回归哨兵：第一版把这类全丢了")
    # 5) 混合：配对 + 孤点 + 线段，总数守恒
    mixed = [ins(0, 0, "a"), rect(0, 0, 240, 240, "b"),
             ins(8000, 0, "c"), line(20000, 0, 20300, 0, "d")]
    chk(u"混合输入总数守恒", mixed, 3, 1,
        u"4 条输入 -> 3 根柱（a+b 并一根、c 孤点、d 线段）")
    # 6) 相邻柱不被错并：间距 5000、截面 400，配对半径 400 不会跨过去
    chk(u"相邻柱不错并",
        [ins(0, 0, "1"), rect(0, 0, 400, 400, "2"), ins(5000, 0, "3"),
         rect(5000, 0, 400, 400, "4")], 2, 2)
    return cases

def eval_level_from_filename():
    u"""图纸文件名 -> 楼层 的真值测试。

    为什么单开：吃一套图的前提是"每张图归对层"。归错了不会报错，
    只会把整层的墙叠到别的层上——比不建还难查。所以这条必须有真值。
    以前只有图层名一条推断路径，而项目楼层普遍叫「标高 3」、
    文件名普遍叫「三层平面图.dwg」，两者配不上，整套图全落到 levels[0]
    （真机实测 2048 堵墙都在标高 1）。
    """
    LV = [u"标高 1", u"标高 2", u"标高 3", u"屋面"]
    # 第二套用**中文层号名**：真机上的楼层普遍就这么叫。上一版只测了"标高 N"，
    # 于是把"子串抢层"整类缺陷测不到 —— 二十四层被读成四层、十二层被读成地下二层
    # （2026-10-13 实测：项目楼层 21~26 层全部被解析成 11~16）。
    LV_CN = [u"地下一层", u"地下二层", u"首层", u"二层", u"三层", u"四层",
             u"五层", u"六层", u"七层", u"八层", u"十一层", u"十二层",
             u"十四层", u"二十层", u"二十四层", u"二十六层",
             u"标准层", u"屋面层"]
    cases = [
        (u"二层平面图.dwg", u"标高 2", u"中文层号"),
        (u"1F平面.dxf", u"标高 1", u"数字F"),
        (u"建施-03 三层平面.dwg", u"标高 3", u"前缀带序号+中文层号"),
        (u"2层梁配筋图.dxf", u"标高 2", u"结构图"),
        (u"屋面层.dwg", u"屋面", u"走原有 token 通道"),
        (u"0目录.dwg", None, u"目录页不该命中"),
        (u"标准层.dwg", None, u"无层号 -> 交回上层兜底并披露"),
        (u"F9.dwg", None, u"项目里没有 9 层，不得乱映射"),
        (u"地下一层平面.dwg", None, u"项目里没有地下层，不得映射到标高 1"),
        # ---- 中文数字词边界（本轮修复的主体）----
        (u"二十四层平面图.dwg", u"二十四层", LV_CN,
         u"24 层不得被当成 4 层（旧版子串匹配的实际结果）"),
        (u"十二层平面图.dwg", u"十二层", LV_CN,
         u"12 层不得被「二层」抢成地下二层"),
        (u"二十六层平面.dwg", u"二十六层", LV_CN, u"21~99 中文数字必须认得"),
        (u"四层平面图.dwg", u"四层", LV_CN, u"单层号仍要正常命中"),
        (u"首层平面图.dwg", u"首层", LV_CN, u"首层=1（旧版返回 None，配不上任何层）"),
        (u"-1层平面.dwg", u"地下一层", LV_CN, u"连字符负层"),
        (u"7-27单数层平面.dwg", u"七层", LV_CN,
         u"奇偶标准层图取范围最低层，并在依据里写明共用一图"),
        (u"5#标准层平面 1:100.dwg", u"标准层", LV_CN,
         u"楼栋号 5# 不得被读成 5 层；无层号时按名匹配"),
        (u"机房屋顶平面.dwg", u"屋面层", LV_CN,
         u"屋顶/屋面/天面是同一层的三种写法"),
        (u"29层平面.dwg", None, LV_CN, u"项目里没有 29 层 -> 拒绝，不许就近凑"),
    ]
    out = []
    for c in cases:
        if len(c) == 4:
            fn, want, lv, why = c
        else:
            fn, want, why = c
            lv = LV
        got = br.level_from_filename(fn, lv)
        out.append({"file": fn, "want": want, "got": got,
                    "ok": got == want, "why": why})
    # 依据文字本身也要能被读出来：范围/奇偶标准层必须说清"取最低层"，
    # 否则用户看到"建在七层"会以为只建了七层，而图上这张是 7~27 层共用图。
    name, ev = br.level_from_text(u"7-27单数层平面", LV_CN)
    out.append({"file": u"范围依据含「取最低层」", "want": u"有",
                "got": (name, ev), "ok": (u"取最低层" in (ev or u"")),
                "why": u"奇偶标准层图必须披露只建了最低层"})
    return out

def eval_door_schedule():
    u"""门窗表解析器的真值测试。

    这个模块此前是孤儿：全仓无人 import，也从没跑过真实图纸。
    2026-10-11 第一次拿 6 张真图跑，结果是不能用（详见 door_schedule.py 注释）。
    现在有了真值，防止再退回去。
    """
    import door_schedule as ds
    import tempfile
    cases = []
    tmpd = tempfile.gettempdir()

    def build(path, cells):
        d = ezdxf.new("R2010")
        d.header["$INSUNITS"] = INSUNITS_MM
        m = d.modelspace()
        for x, y, t in cells:
            m.add_text(t, dxfattribs={"layer": "0", "height": 300.0,
                                      "insert": (x, y, 0)})
        d.saveas(path)

    # 版式 A：宽、高是**两个独立单元格**（真图 r6 实测的主流版式）
    pA = os.path.join(tmpd, "sch_A.dxf")
    build(pA, [
        (30000, 113400, u"门窗表"),
        (35900, 106600, u"4"), (37400, 106600, u"M4"),
        (38700, 106600, u"1500"), (40200, 106600, u"2700"),
        (42000, 106600, u"14"), (43500, 106600, u"塑钢推拉门"),
        (35900, 104600, u"6"), (37400, 104600, u"C1"),
        (38700, 104600, u"3000"), (40200, 104600, u"2700"),
        (42000, 104600, u"12"), (43500, 104600, u"塑钢窗"),
    ])
    rA = ds.parse_dxf(pA)
    byc = dict((i["code"], i) for i in rA["items"])
    cases.append({"name": u"分列版式：门行宽高正确",
                  "ok": (byc.get("M4", {}).get("width_mm") == 1500
                         and byc.get("M4", {}).get("height_mm") == 2700
                         and byc.get("M4", {}).get("kind") == "door"),
                  "got": byc.get("M4")})
    cases.append({"name": u"分列版式：C 前缀判为窗（旧版全判成门）",
                  "ok": (byc.get("C1", {}).get("kind") == "window"
                         and byc.get("C1", {}).get("width_mm") == 3000),
                  "got": byc.get("C1")})
    cases.append({"name": u"分列版式：数量列不被当成尺寸",
                  "ok": (byc.get("C1", {}).get("height_mm") == 2700
                         and byc.get("C1", {}).get("total") == u"12"),
                  "got": byc.get("C1")})

    # 版式 B：一格内写 "1000x2100"（旧代码唯一认的版式，不能退化）
    pB = os.path.join(tmpd, "sch_B.dxf")
    build(pB, [
        (30000, 50000, u"门窗表"),
        (37400, 49000, u"M1"), (38700, 49000, u"1000x2100"),
        (42000, 49000, u"6"), (43500, 49000, u"防盗门"),
    ])
    rB = ds.parse_dxf(pB)
    it = rB["items"][0] if rB["items"] else {}
    cases.append({"name": u"合并版式 1000x2100 仍可读",
                  "ok": (it.get("width_mm") == 1000 and it.get("height_mm") == 2100
                         and it.get("kind") == "door"),
                  "got": it})

    # 负例：没有门窗表标题时**必须一条不出**。
    # r4 实测没有表、只有设计说明，旧代码从说明里造出 26 条自相矛盾的行。
    pC = os.path.join(tmpd, "sch_C.dxf")
    build(pC, [
        (10000, 40000, u"3.门窗尺寸，划分和要求均详装修图"),
        (10000, 39000, u"M0921"), (11000, 39000, u"400x400"),
        (12000, 39000, u"铝合金窗"),
    ])
    rC = ds.parse_dxf(pC)
    cases.append({"name": u"无门窗表标题时不得造数据",
                  "ok": (len(rC["items"]) == 0 and not rC["title"]),
                  "got": {"n": len(rC["items"]), "title": rC["title"]}})

    for p in (pA, pB, pC):
        try:
            os.remove(p)
        except OSError:
            pass
    return cases

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", help="把结果写成 JSON")
    ap.add_argument("--gate", action="store_true",
                    help="回归闸门：对照组必须满分，已知缺陷台账必须仍在（见 _gate）")
    args = ap.parse_args()

    print("规则: %s" % RULES)
    print("场景数: %d\n" % len(SCENARIOS))
    results = []
    for name, kw in SCENARIOS:
        r = evaluate(name, **kw)
        results.append(r)
        if r.get("fatal"):
            print("[%s] 链路致命错误: %s" % (name, r["fatal"]))
            print("       复现件保留在: %s" % r.get("kept_dxf"))
            continue
        print("[%s]  图元 %d（注释滤 %d / 短线滤 %d）"
              % (name, r["entities"], r["filtered_annotation"], r["filtered_short"]))
        print("   %-9s %7s %7s %7s %9s %9s" % ("类型", "应有", "实得", "命中", "准确率", "召回率"))
        for t in TYPES:
            v = r["types"][t]
            print("   %-9s %7d %7d %7d %8.0f%% %8.0f%%"
                  % (t, v["expected"], v["planned"], v["tp"],
                     v["precision"] * 100, v["recall"] * 100))
        print("   屋面明确不建: 期望 %d / 实际标记 %d" %
              (r["excluded_expected"], r["excluded_found"]))
        print("   块炸开重复建造: %d 个" % r.get("duplicated_from_block", -1))
        print(u"   窗层细节线应排除: 期望 %d / 实际排除 %d"
              % (r.get("detail_expected", -1), r.get("detail_excluded", -1)))
        print("   墙中线长度: 期望 %s mm / 计划 %s mm / 误差 %s%%"
              % (r["wall_len_expected_mm"], r["wall_len_planned_mm"],
                 r["wall_len_err_pct"]))
        print("")

    merge_cases = eval_wall_merge()
    print(u"—— 墙几何合并真值（consolidate_walls）——")
    for c in merge_cases:
        print(u"   [%s] %-16s 期望 %d 面 / 实得 %d 面  长度 期望 %s 实得 %s  厚度 %s  %s"
              % (u"OK " if c["ok"] else u"坏", c["name"], c["want_n"], c["got_n"],
                 c["want_len"], c["got_len"], c["thks"], c["note"]))
    col_cases = eval_column_merge()
    print(u"—— 柱标记/轮廓归并真值（_consolidate_columns）——")
    for c in col_cases:
        print(u"   [%s] %-18s 期望 %d 根 / 实得 %d 根  带截面 期望 %s 实得 %s  %s"
              % (u"OK " if c["ok"] else u"坏", c["name"], c["want_n"], c["got_n"],
                 c["want_len"], c["got_len"], c["note"]))
    sch_cases = eval_door_schedule()
    print(u"—— 门窗表解析真值 ——")
    for c in sch_cases:
        print(u"   [%s] %-28s %s" % (u"OK " if c["ok"] else u"坏", c["name"],
                                     str(c.get("got"))[:70]))
    lvl_cases = eval_level_from_filename()
    print(u"—— 图纸文件名 -> 楼层 真值 ——")
    for c in lvl_cases:
        print(u"   [%s] %-22s -> %-8s (期望 %-8s) %s"
              % (u"OK " if c["ok"] else u"坏", c["file"], c["got"] or u"None",
                 c["want"] or u"None", c["why"]))
    grid_cases = eval_grid_merge()
    print(u"—— 轴网归并真值（consolidate_grids）——")
    for c in grid_cases:
        print(u"   [%s] %-16s 期望 %d 条 / 实得 %d 条  总长 期望 %s 实得 %s  %s"
              % (u"OK " if c["ok"] else u"坏", c["name"], c["want_n"], c["got_n"],
                 c["want_len"], c["got_len"], c["note"]))
    if args.json:
        with open(args.json, "wb") as f:
            f.write(json.dumps({"classification": results, "wall_merge": merge_cases,
                                "grid_merge": grid_cases, "column_merge": col_cases,
                                "level_from_filename": lvl_cases, "door_schedule": sch_cases},
                               ensure_ascii=False, indent=1).encode("utf-8"))
        print(u"已写出 %s" % args.json)

    if args.gate:
        rc = _gate(results)
        broken = [c["name"] for c in merge_cases if not c["ok"]]
        broken += [c["name"] for c in grid_cases if not c["ok"]]
        broken += [c["name"] for c in col_cases if not c["ok"]]
        broken += [c["file"] for c in lvl_cases if not c["ok"]]
        broken += [c["name"] for c in sch_cases if not c["ok"]]
        if broken:
            print(u"[GATE FAIL] 几何合并真值不通过: " + u"、".join(broken))
            return 1
        return rc
    return 0


def _gate(results):
    u"""闸门分两层，故意不对称：

      1) 硬失败——链路崩、或对照组（图层/块名有语义、单位是 mm）不完美。
         对照组只考规则本身，它退化 = 我们把 reader 或 CSV 改坏了，零容忍。
      2) 已知缺陷台账——不判失败，但必须逐项复现。若某条"好了"，
         说明有人修了它却忘了删台账，同样报错（否则台账会烂掉）。

    不拿"真实图纸覆盖率"当闸门：那个指标要打折看，未匹配里大半是植物图签，
    本来就不该建。合成真值才有精确率/召回率可谈。
    """
    by = dict((r["scenario"], r) for r in results)
    bad = []

    for r in results:
        if r.get("fatal"):
            bad.append(u"%s 链路致命: %s" % (r["scenario"], r["fatal"]))
    for name in CONTROL:
        r = by.get(name)
        if r is None or r.get("fatal"):
            bad.append(u"对照组 %s 没跑出结果" % name)
            continue
        for t in TYPES:
            v = r["types"][t]
            if v["expected"] and v["recall"] < 1.0:
                bad.append(u"%s/%s 召回 %.0f%%（对照组必须 100%%）"
                           % (name, t, v["recall"] * 100))
            if v["planned"] and v["precision"] < 1.0:
                bad.append(u"%s/%s 精确 %.0f%%（对照组不该有多建的）"
                           % (name, t, v["precision"] * 100))
        if r.get("detail_excluded") != r.get("detail_expected"):
            bad.append(u"%s 窗层细节线只排除了 %s/%s（每个窗会多建假窗）"
                       % (name, r.get("detail_excluded"),
                          r.get("detail_expected")))
        if r.get("excluded_found") != r.get("excluded_expected"):
            bad.append(u"%s 屋面『明确不建』只标了 %s/%s"
                       % (name, r.get("excluded_found"), r.get("excluded_expected")))

    print(u"—— 已知缺陷台账（不判失败，但必须还在）——")
    for k in KNOWN_DEFECTS:
        r = by.get(k["scenario"])
        cur = _metric(r, k["type"], k["metric"]) if r else None
        moved = (cur is None or abs(cur - k["value"]) > 0.005)
        tag = u"⚠ 已变化(现 %s)" % cur if moved else u"仍在"
        if moved and cur is not None and cur > k["value"]:
            bad.append(u"台账条目 %s/%s/%s 现为 %.2f（记录值 %.2f）——"
                       u"若确已修好，请删掉这条 KNOWN_DEFECTS"
                       % (k["scenario"], k["type"], k["metric"], cur, k["value"]))
        print(u"   [%s] %-16s %-7s %-9s 记录 %.2f ｜ %s"
              % (tag, k["scenario"], k["type"], k["metric"], k["value"], k["why"]))

    print("")
    if bad:
        print(u"[GATE FAIL] " + u"；".join(bad))
        return 1
    print(u"[GATE OK] 对照组满分，台账 %d 条缺陷仍在（别把它们当装饰）"
          % len(KNOWN_DEFECTS))
    return 0


if __name__ == "__main__":
    sys.exit(main())
