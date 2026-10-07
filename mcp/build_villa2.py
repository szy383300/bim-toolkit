# -*- coding: utf-8 -*-
u"""四层别墅终版建模 v2.1 —— 全 execute_code 路线（桥冻结 7.10.4）。

探针结论(2026-09-12, 19 轮):
  - 楼梯(已实证): DB.StairsEditScope.Start(blId, tlId) 双参新建;
    事务在 Start 之后开且挂预处理器; StairsRun.CreateStraightRun x2;
    t.Commit() -> scope.Commit(pre) —— 2019 唯一签名是
    Commit(IFailuresPreprocessor), 裸调必报 "takes exactly 1 argument";
    CreateAutomaticLanding 对分离/相接梯段一律拒收(死路), 平台用
    楼板补(标高偏移+1500); 失败走 scope.Cancel()
  - 预处理器: 3 轮上限血泪教训; 只 Resolve 错误级( IList[FailureMessageAccessor] );
    空消息返回 Continue(否则触发重试回滚循环)
  - import clr 必须放在 Autodesk 导入之后, 否则破坏
    import ... as ARCH 的模块绑定(StairsRunJustification 在 ARCH 命名空间)
  - 屋面: FootPrintRoof 对本档 RoofType 全报 null(弃用);
    NewExtrusionRoof 三角截面, 参考面 cutVector=水平法线
  - 门窗: 桥的寄宿检查是 3D 距离(z=0 与层高恒差 4000 恒拒收) -> 自建,
    直接宿主, 类型按宽度匹配; 【2026-09-12 实锤】 4 参 NewFamilyInstance
    把实例标高绑到 标高1 且无视 ppt.z 垂直分量 -> L2+ 门窗全部悬浮在 z~0
    (不切割=假活, 切割越界=提交被删); 必须 5 参重载
    NewFamilyInstance(ppt, sym, wall, lvl, st) 且 ppt.z=标高海拔+底高度
  - 标高2 模板自带 4000mm -> 改 LEVEL_ELEV 到 3000
  - exec 源码必须纯 ASCII(中文转 \\uXXXX); SystemFamilyType.Name 在
    exec 里读不了(勿信 noName)
"""
import json
import sys

sys.path.insert(0, r"E:\bim-toolkit\mcp")
from bridge_client import call  # noqa: E402

SAVE_PATH = r"E:\bim-toolkit\models\四层别墅.rvt"
ID = [2000]
EXT_T, INT_T, H = 240, 100, 3000
LVL = [u"标高 1", u"标高 2", u"标高 3", u"标高 4", u"屋顶"]

# 注入所有 exec 片段的失败预处理器(实证配方, 3 轮上限见 bridge_core)
PRE_SRC = u'''from Autodesk.Revit.DB import (IFailuresPreprocessor,
                               FailureProcessingResult, FailureSeverity,
                               FailureMessageAccessor)
from System.Collections.Generic import List as _DL

class _Pre(IFailuresPreprocessor):
    def PreprocessFailures(self, fa):
        try:
            ms = list(fa.GetFailureMessages())
        except Exception:
            ms = []
        if ms:
            try:
                errs = _DL[FailureMessageAccessor]()
                for m in ms:
                    if m.GetSeverity() == FailureSeverity.Error:
                        errs.Add(m)
                try:
                    fa.DeleteAllWarnings()
                except Exception:
                    pass
                if errs.Count > 0:
                    fa.ResolveFailures(errs)
                    return FailureProcessingResult.ProceedWithCommit
            except Exception:
                pass
        return FailureProcessingResult.Continue

def _prep(tr):
    pre = _Pre()
    try:
        fo = tr.GetFailureHandlingOptions()
        fo.SetFailuresPreprocessor(pre)
        tr.SetFailureHandlingOptions(fo)
    except Exception:
        pass
    return pre
'''


def esc(s):
    return u"".join(c if ord(c) < 128 else "\\u%04x" % ord(c)
                    for c in s)


def run(code, label, timeout=280):
    code = code.replace("__PRE__", PRE_SRC)
    ID[0] += 1
    r = call({"type": "execute_code", "id": ID[0],
              "code": esc(code), "no_transaction": True},
             timeout=timeout)
    res = r.get("result", r)
    print(u"[%s] %s" % (label, json.dumps(res, ensure_ascii=False)[:700]))
    return res


def bridge(cmd_type, payload, label, timeout=280):
    ID[0] += 1
    cmd = {"type": cmd_type, "id": ID[0]}
    cmd.update(payload)
    r = call(cmd, timeout=timeout)
    res = r.get("result", r)
    print(u"[%s] %s" % (label, json.dumps(res, ensure_ascii=False)[:300]))
    return res


def ext_walls():
    return [[[0, 0], [12000, 0]], [[0, 9000], [12000, 9000]],
            [[0, 0], [0, 9000]], [[12000, 0], [12000, 9000]]]


def west_walls():
    return [[[2400, 0], [2400, 9000]],
            [[0, 3000], [2400, 3000]],
            [[0, 6900], [2400, 6900]]]


def l1_walls():
    return west_walls() + [[[8400, 0], [8400, 9000]],
                           [[2400, 5200], [8400, 5200]],
                           [[8400, 4500], [12000, 4500]]]


def l23_walls():
    return west_walls() + [[[7400, 0], [7400, 9000]],
                           [[2400, 4500], [12000, 4500]],
                           [[7400, 2700], [12000, 2700]]]


def l4_walls():
    return west_walls() + [[[7400, 0], [7400, 9000]],
                           [[7400, 2700], [12000, 2700]]]


def walls_batch(walls, lvl, t):
    return [{"start": w[0], "end": w[1], "height_mm": H,
             "thickness_mm": t, "level": lvl} for w in walls]


FP = [[0, 0], [12000, 0], [12000, 9000], [0, 9000]]
BAL = [[4200, 0], [9000, 0], [9000, -1500], [4200, -1500]]
RAIL_PATH = [[4200, 0], [4200, -1500], [9000, -1500], [9000, 0]]


def dw(kind, x, y, w, level, h=None, sill=None):
    o = {"kind": kind, "point": [x, y], "width_mm": w,
         "height_mm": h or (2100 if kind == "door" else 1500),
         "level": level}
    if sill:
        o["sill_mm"] = sill
    return o


def openings():
    ops = []
    ops += [dw("door", 1200, 0, 1200, LVL[0]),
            dw("window", 5400, 0, 1800, LVL[0], sill=900),
            dw("window", 10200, 0, 1500, LVL[0], sill=900),
            dw("window", 10200, 9000, 1500, LVL[0], sill=900),
            dw("window", 5400, 9000, 1500, LVL[0], sill=900),
            dw("window", 0, 7800, 600, LVL[0], sill=1500),
            dw("door", 2400, 1500, 900, LVL[0]),
            dw("door", 2400, 4800, 900, LVL[0]),
            dw("door", 2400, 7800, 900, LVL[0]),
            dw("door", 5400, 5200, 900, LVL[0]),
            dw("door", 8400, 2200, 900, LVL[0]),
            dw("door", 10200, 4500, 900, LVL[0])]
    for lvl in (LVL[1], LVL[2]):
        ops += [dw("door", 6600, 0, 1800, lvl),
                dw("window", 3600, 0, 1500, lvl, sill=900),
                dw("window", 9700, 0, 600, lvl, sill=1500),
                dw("window", 4900, 9000, 1500, lvl, sill=900),
                dw("window", 9700, 9000, 1500, lvl, sill=900),
                dw("window", 12000, 6700, 1500, lvl, sill=900),
                dw("window", 0, 7800, 600, lvl, sill=1500),
                dw("door", 2400, 4800, 900, lvl),
                dw("door", 2400, 3800, 900, lvl),
                dw("door", 1200, 6900, 900, lvl),
                dw("door", 1200, 3000, 900, lvl),
                dw("door", 7400, 3600, 900, lvl),
                dw("door", 9700, 4500, 900, lvl),
                dw("door", 9700, 2700, 900, lvl)]
    ops += [dw("window", 4900, 0, 1800, LVL[3], sill=900),
            dw("window", 9700, 0, 1500, LVL[3], sill=900),
            dw("window", 4900, 9000, 1800, LVL[3], sill=900),
            dw("window", 9700, 9000, 1500, LVL[3], sill=900),
            dw("window", 12000, 6700, 1500, LVL[3], sill=900),
            dw("window", 0, 7800, 600, LVL[3], sill=1500),
            dw("door", 2400, 4800, 900, LVL[3]),
            dw("door", 7400, 1350, 900, LVL[3]),
            dw("door", 7400, 5000, 900, LVL[3])]
    return ops


WIPE = u'''# -*- coding: utf-8 -*-
__PRE__
cats = ["OST_Walls", "OST_Doors", "OST_Windows", "OST_Floors",
        "OST_Roofs", "OST_Stairs", "OST_StairsRuns", "OST_StairsLandings",
        "OST_Railings", "OST_Lines"]
ids = []
for cname in cats:
    try:
        bic = getattr(BuiltInCategory, cname)
    except Exception:
        continue
    for e in FilteredElementCollector(doc).OfCategory(bic).ToElements():
        try:
            if isinstance(e, ElementType):
                continue
            ids.append(e.Id)
        except Exception:
            pass
t = Transaction(doc, "villa wipe")
pre = _prep(t)
t.Start()
deleted = 0
for i in ids:
    try:
        doc.Delete(i)
        deleted += 1
    except Exception:
        pass
t.Commit()
_result = {"found": len(ids), "deleted": deleted}
'''

FIX_LEVEL = u'''# -*- coding: utf-8 -*-
__PRE__
lvls = sorted(FilteredElementCollector(doc).OfClass(Level).ToElements(),
              key=lambda l: l.Elevation)
t = Transaction(doc, "fix levels")
pre = _prep(t)
t.Start()
fixed = []
for idx, mm in ((1, 3000.0),):
    lv = lvls[idx]
    p = lv.get_Parameter(BuiltInParameter.LEVEL_ELEV)
    cur_mm = lv.Elevation / 0.00328084
    if abs(cur_mm - mm) > 50:
        if p is not None and not p.IsReadOnly:
            p.Set(mm * 0.00328084)
            fixed.append([idx, int(cur_mm), int(mm)])
t.Commit()
_result = {"fixed": fixed,
           "elevs_mm": [round(l.Elevation / 0.00328084) for l in lvls]}
'''

STAIRS = u'''# -*- coding: utf-8 -*-
import Autodesk.Revit.DB as DB
import Autodesk.Revit.DB.Architecture as ARCH
__PRE__
out = {}
lvls = sorted(FilteredElementCollector(doc).OfClass(Level).ToElements(),
              key=lambda l: l.Elevation)
b = __B__
bl, tl = lvls[b], lvls[b + 1]
ft = 0.00328084
just = ARCH.StairsRunJustification.Center
scope = DB.StairsEditScope(doc, "villa stairs")
sid = scope.Start(bl.Id, tl.Id)
out["stairs"] = int(sid.IntegerValue)
t = Transaction(doc, "villa stairs txn")
pre = _prep(t)
t.Start()
try:
    run1 = ARCH.StairsRun.CreateStraightRun(
        doc, sid,
        Line.CreateBound(XYZ(600 * ft, 3300 * ft, bl.Elevation),
                         XYZ(600 * ft, 5100 * ft, bl.Elevation)), just)
    out["run1"] = int(run1.Id.IntegerValue)
    run2 = ARCH.StairsRun.CreateStraightRun(
        doc, sid,
        Line.CreateBound(XYZ(1800 * ft, 6300 * ft, bl.Elevation),
                         XYZ(1800 * ft, 4500 * ft, bl.Elevation)), just)
    out["run2"] = int(run2.Id.IntegerValue)
    t.Commit()
    out["txn_ok"] = True
    scope.Commit(pre)
    out["scope_ok"] = True
    st = doc.GetElement(sid)
    out["runs"] = len(list(st.GetStairsRuns()))
    out["landings"] = len(list(st.GetStairsLandings()))
except Exception as ex:
    out["err"] = to_text(ex)[:250]
    try:
        t.RollBack()
    except Exception:
        pass
    try:
        scope.Cancel()
        out["cancelled"] = True
    except Exception as ex2:
        out["cancel_err"] = to_text(ex2)[:120]
_result = out
'''

LANDING = u'''# -*- coding: utf-8 -*-
__PRE__
out = {}
lvls = sorted(FilteredElementCollector(doc).OfClass(Level).ToElements(),
              key=lambda l: l.Elevation)
b = __B__
bl = lvls[b]
ft = 0.00328084
ftypes = list(FilteredElementCollector(doc).OfClass(FloorType)
              .ToElements())
t = Transaction(doc, "villa landing")
pre = _prep(t)
t.Start()
try:
    pts = [(0, 5100), (1200, 5100), (1200, 6300), (0, 6300)]
    arr = CurveArray()
    cs = [XYZ(x * ft, y * ft, bl.Elevation) for x, y in pts]
    for i in range(4):
        arr.Append(Line.CreateBound(cs[i], cs[(i + 1) % 4]))
    fl = doc.Create.NewFloor(arr, ftypes[0], bl, False)
    p = fl.get_Parameter(BuiltInParameter.FLOOR_HEIGHTABOVELEVEL_PARAM)
    if p is not None and not p.IsReadOnly:
        p.Set(1500 * ft)
        out["offset"] = 1500
    t.Commit()
    out["id"] = int(fl.Id.IntegerValue)
except Exception as ex:
    out["err"] = to_text(ex)[:200]
    try:
        t.RollBack()
    except Exception:
        pass
_result = out
'''

ROOF = u'''# -*- coding: utf-8 -*-
from Autodesk.Revit.DB import CurveArray, XYZ, Line
__PRE__
out = {}
lvls = sorted(FilteredElementCollector(doc).OfClass(Level).ToElements(),
              key=lambda l: l.Elevation)
lvl = lvls[-1]
rts = list(FilteredElementCollector(doc).OfClass(RoofType).ToElements())
rt = rts[0]
views = list(FilteredElementCollector(doc).OfClass(ViewPlan).ToElements())
v = views[0]
ft = 0.00328084
z0 = lvl.Elevation
t = Transaction(doc, "villa roof")
pre = _prep(t)
t.Start()
try:
    rt2 = rt.Duplicate("Villa_Roof_Type")
except Exception:
    rt2 = rt
made = None
errs = []
for tag, rev in (("fwd", False), ("rev", True)):
    try:
        rp = doc.Create.NewReferencePlane2(
            XYZ(6000 * ft, -600 * ft, 0), XYZ(6000 * ft, 9600 * ft, 0),
            XYZ(0, 0, 1), v)
        tri = CurveArray()
        rise = 5100.0 * 0.5774 * ft
        a = XYZ(6000 * ft, -600 * ft, z0)
        b = XYZ(6000 * ft, 4500 * ft, z0 + rise)
        c = XYZ(6000 * ft, 9600 * ft, z0)
        segs = [(a, b), (b, c), (c, a)]
        if rev:
            segs = [(q, p) for (p, q) in reversed(segs)]
        for p, q in segs:
            tri.Append(Line.CreateBound(p, q))
        roof = doc.Create.NewExtrusionRoof(
            tri, rp, lvl, rt2, -6600.0 * ft, 6600.0 * ft)
        made = {"variant": tag, "id": int(roof.Id.IntegerValue)}
        break
    except Exception as ex:
        errs.append(tag + ": " + to_text(ex)[:120])
out["made"] = made
out["errs"] = errs[:4]
if made:
    t.Commit()
else:
    t.RollBack()
_result = out
'''

RAILING = u'''# -*- coding: utf-8 -*-
import Autodesk.Revit.DB.Architecture as ARCH
__PRE__
out = {}
lvls = sorted(FilteredElementCollector(doc).OfClass(Level).ToElements(),
              key=lambda l: l.Elevation)
lvl = lvls[__B__]
rts = list(FilteredElementCollector(doc).OfClass(ARCH.RailingType)
           .ToElements())
ft = 0.00328084
path = __PATH__
t = Transaction(doc, "villa railing")
pre = _prep(t)
t.Start()
made = None
errs = []
for rt in rts:
    try:
        corners = [XYZ(x * ft, y * ft, lvl.Elevation) for x, y in path]
        cl = CurveLoop.Create([Line.CreateBound(corners[i], corners[i + 1])
                               for i in range(len(corners) - 1)])
        r = ARCH.Railing.Create(doc, cl, rt.Id, lvl.Id)
        made = int(r.Id.IntegerValue)
        break
    except Exception as ex:
        errs.append(to_text(ex)[:100])
out["id"] = made
out["errs"] = errs[:3]
if made:
    t.Commit()
else:
    t.RollBack()
_result = out
'''

DOORS = u'''# -*- coding: utf-8 -*-
import Autodesk.Revit.DB as DB
from Autodesk.Revit.DB.Structure import StructuralType
__PRE__
out = {"placed": 0, "fails": []}
ft = 0.00328084
lvls = sorted(FilteredElementCollector(doc).OfClass(Level).ToElements(),
              key=lambda l: l.Elevation)
ops = __OPS__
kind = "__KIND__"
cat = BuiltInCategory.OST_Doors if kind == "door" \\
    else BuiltInCategory.OST_Windows
syms = []
for e in FilteredElementCollector(doc).OfCategory(cat).ToElements():
    if isinstance(e, FamilySymbol):
        syms.append(e)
if len(syms) == 0:
    out["fails"].append("no symbols")
    _result = out
else:
    t = Transaction(doc, "villa " + kind)
    pre = _prep(t)
    t.Start()
    for s in syms:
        try:
            s.Activate()
        except Exception:
            pass
    placed_ids = []
    for op in ops:
        try:
            px, py = op["point"][0] * ft, op["point"][1] * ft
            want_w = op.get("width_mm", 900)
            want_h = op.get("height_mm", 2100)
            sill = op.get("sill_mm", 0)
            lvl = lvls[op.get("li", 0)]
            best = None
            best_d = 1e18
            for w in FilteredElementCollector(doc).OfClass(Wall) \\
                    .ToElements():
                p = w.get_Parameter(
                    BuiltInParameter.WALL_BASE_CONSTRAINT)
                if p is None or p.AsElementId() is None:
                    continue
                if p.AsElementId().IntegerValue != lvl.Id.IntegerValue:
                    continue
                c = w.Location.Curve
                r = c.Project(XYZ(px, py, lvl.Elevation))
                if r is None:
                    continue
                dxy = (r.XYZPoint.X - px) ** 2 + \\
                    (r.XYZPoint.Y - py) ** 2
                if dxy < best_d:
                    best_d = dxy
                    best = (w, r.XYZPoint)
            if best is None or best_d > (300 * ft) ** 2:
                out["fails"].append("op %s: no wall" % str(op["point"]))
                continue
            w, ppt = best
            sym = None
            for s in syms:
                try:
                    wp = s.get_Parameter(
                        BuiltInParameter.FAMILY_WIDTH_PARAM)
                    if wp is not None and \\
                            abs(wp.AsDouble() / ft - want_w) < 60:
                        sym = s
                        break
                except Exception:
                    pass
            if sym is None:
                sym = syms[0]
            inst = doc.Create.NewFamilyInstance(
                XYZ(ppt.X, ppt.Y, lvl.Elevation + sill * ft),
                sym, w, lvl, StructuralType.NonStructural)
            if inst is None:
                out["fails"].append("op %s: none" % str(op["point"]))
                continue
            for pn, val in ((u"\\u5bbd\\u5ea6", want_w * ft),
                            (u"\\u9ad8\\u5ea6", want_h * ft)):
                try:
                    ip = inst.LookupParameter(pn)
                    if ip is not None and not ip.IsReadOnly:
                        ip.Set(val)
                except Exception:
                    pass
            if sill:
                try:
                    ip = inst.LookupParameter(u"\\u5e95\\u9ad8\\u5ea6")
                    if ip is not None and not ip.IsReadOnly:
                        ip.Set(sill * ft)
                except Exception:
                    pass
            placed_ids.append(int(inst.Id.IntegerValue))
            out["placed"] += 1
        except Exception as ex:
            out["fails"].append("op %s: %s" % (
                str(op["point"]), to_text(ex)[:100]))
    t.Commit()
    out["ids"] = placed_ids[:8]
    _result = out
'''

VERIFY = u'''# -*- coding: utf-8 -*-
out = {}
for cname in ["OST_Walls", "OST_Doors", "OST_Windows", "OST_Floors",
              "OST_Roofs", "OST_Stairs", "OST_Railings"]:
    try:
        bic = getattr(BuiltInCategory, cname)
        n = 0
        for e in FilteredElementCollector(doc).OfCategory(bic).ToElements():
            if not isinstance(e, ElementType):
                n += 1
        out[cname] = n
    except Exception:
        out[cname] = "ERR"
_result = out
'''


def main():
    r = call({"type": "ping", "id": 1}, timeout=15)
    print(u"[ping]", json.dumps(r.get("result", r), ensure_ascii=False))

    run(WIPE, "清场")
    run(FIX_LEVEL, "标高修正")

    ext_ids, int_ids = [], []
    for lvl in LVL[:4]:
        res = bridge("create_walls",
                     {"walls": walls_batch(ext_walls(), lvl, EXT_T)},
                     "外墙" + lvl)
        ext_ids += res.get("ids") or []
    for walls, lvl in ((l1_walls(), LVL[0]), (l23_walls(), LVL[1]),
                       (l23_walls(), LVL[2]), (l4_walls(), LVL[3])):
        res = bridge("create_walls",
                     {"walls": walls_batch(walls, lvl, INT_T)},
                     "内墙" + lvl)
        int_ids += res.get("ids") or []

    floors = [{"points": FP, "level": LVL[i], "structural": (i == 0)}
              for i in range(4)]
    floors += [{"points": BAL, "level": LVL[1]},
               {"points": BAL, "level": LVL[2]}]
    res = bridge("create_floors", {"floors": floors}, "楼板")
    fl_ids = res.get("ids") or []
    bal_ids = fl_ids[-2:] if len(fl_ids) >= 6 else []

    for b in (0, 1, 2):
        run(STAIRS.replace("__B__", str(b)), "楼梯%d" % (b + 1))
        run(LANDING.replace("__B__", str(b)), "平台%d" % (b + 1))

    res_roof = run(ROOF, "屋面")
    roof_id = (res_roof.get("made") or {}).get("id")

    for b in (1, 2):
        code = RAILING.replace("__B__", str(b)).replace(
            "__PATH__", str(RAIL_PATH))
        run(code, "栏杆标高%d" % (b + 1))

    li_map = {LVL[0]: 0, LVL[1]: 1, LVL[2]: 2, LVL[3]: 3}
    for kind in ("door", "window"):
        ops = [o for o in openings() if o["kind"] == kind]
        for o in ops:
            o["li"] = li_map[o["level"]]
        code = DOORS.replace("__OPS__", json.dumps(ops)).replace(
            "__KIND__", kind)
        run(code, "门窗-" + kind)

    probe = bridge("set_material", {"name": "___probe___"}, "材质探测")
    avail = []
    err = probe.get("error", "")
    if u"available" in err:
        avail = [s.strip() for s in
                 err.split(u"available:")[-1].split(u",") if s.strip()]
    print(u"[可用材质]", u", ".join(avail)[:220])

    def pick(kws):
        for kw in kws:
            for a in avail:
                if kw in a:
                    return a
        return None

    def apply(m, ids, label):
        if not m or not ids:
            print(u"[材质] %s 跳过" % label)
            return
        res = bridge("set_material", {"name": m, "ids": ids},
                     "材质" + label)
        print(u"[材质] %s <- %s assigned=%s" % (
            label, m, res.get("assigned")))

    apply(pick([u"砖", u"砌块", u"混凝土"]), ext_ids, u"外墙")
    apply(pick([u"涂料", u"石膏"]), int_ids, u"内墙")
    if roof_id:
        apply(pick([u"瓦", u"沥青", u"混凝土"]), [roof_id], u"屋面")
    apply(pick([u"木", u"混凝土"]), bal_ids, u"阳台")

    res = bridge("save_document", {"path": SAVE_PATH}, "保存")
    print(u"[保存]", json.dumps(res, ensure_ascii=False)[:200])

    run(VERIFY, "验收")


if __name__ == "__main__":
    main()
