# -*- coding: utf-8 -*-
u"""genbuild.macros - proven Revit 2019 recipes as parameterized
execute_code templates. All templates are pure-ASCII after json
injection (ensure_ascii=True); every write txn carries the failure
preprocessor; failure paths always RollBack / scope.Cancel.

Recipes (2026-09-12 proven on bridge 7.10.4):
  - stairs: DB.StairsEditScope.Start(blId, tlId) two-arg new; txn opens
    AFTER Start; 2x ARCH.StairsRun.CreateStraightRun; t.Commit() ->
    scope.Commit(pre) - 2019 ONLY signature Commit(IFailuresPreprocessor)
  - landing: NewFloor(CurveArray+Append) + FLOOR_HEIGHTABOVELEVEL_PARAM
  - doors/windows: 5-arg NewFamilyInstance(ppt, sym, wall, lvl, st) with
    ppt.z = lvl.Elevation + sill (4-arg binds level to L1 and ignores
    ppt.z vertically -> all L2+ instances float at z~0); nearest-width
    type match; audit = per-instance bbz inside host wall z-range
  - roof: NewExtrusionRoof attempt matrix -> flat NewFloor fallback
          (2026-10-02: 该兜底会标 degraded="floor"，引擎须告警 —— 见 _stage_roofs)
  - railing: ARCH.Railing.Create(doc, CurveLoop, typeId, lvlId)
  - LoadFamily MUST run inside an open transaction
"""
import json

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


def J(obj):
    """ASCII-safe json text for inline injection."""
    return json.dumps(obj, ensure_ascii=True)


# ---------------------------------------------------------------- wipe
WIPE_AND_LEVELS = u'''# -*- coding: utf-8 -*-
__PRE__
import json
G = json.loads(r\'__G__\')
out = {"deleted": 0, "fixed": []}
ids = []
for cname in G["cats"]:
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
t = Transaction(doc, "gen wipe")
pre = _prep(t)
t.Start()
for i in ids:
    try:
        doc.Delete(i)
        out["deleted"] += 1
    except Exception:
        pass
t.Commit()
t2 = Transaction(doc, "gen level fixes")
pre2 = _prep(t2)
t2.Start()
for nm, mm in G["fixes"]:
    hit = None
    for l in FilteredElementCollector(doc).OfClass(Level).ToElements():
        if to_text(l.Name) == nm:
            hit = l
            break
    if hit is None:
        try:
            nl = Level.Create(doc, mm * 0.00328084)
            nl.Name = nm
            out["fixed"].append([nm, "created"])
        except Exception as ex:
            out["fixed"].append([nm, "create-err", to_text(ex)[:60]])
        continue
    cur = round(hit.Elevation / 0.00328084)
    if abs(cur - mm) > 50:
        p = hit.get_Parameter(BuiltInParameter.LEVEL_ELEV)
        if p is not None and not p.IsReadOnly:
            p.Set(mm * 0.00328084)
            out["fixed"].append([nm, cur, mm])
    else:
        out["fixed"].append([nm, "ok", cur])
t2.Commit()
_result = out
'''


def wipe_and_levels(cats, fixes):
    return WIPE_AND_LEVELS.replace("__G__", J(
        {"cats": cats, "fixes": fixes}))


# -------------------------------------------------------------- grids
# 轴网: Grid.Create(BoundLine) + 改名; WIPE_CATS 已含 OST_Grids,
# 重跑前清场 -> 不会因改名冲突翻倍。from/to 为 [x, y] mm。
GRIDS_CREATE = u'''# -*- coding: utf-8 -*-
import Autodesk.Revit.DB as DB
__PRE__
import json
G = json.loads(r\'__G__\')
out = {"made": [], "err": []}
t = Transaction(doc, "gen grids")
pre = _prep(t)
t.Start()
try:
    for g in G["grids"]:
        try:
            p1 = XYZ(float(g["from"][0]) * 0.00328084,
                     float(g["from"][1]) * 0.00328084, 0)
            p2 = XYZ(float(g["to"][0]) * 0.00328084,
                     float(g["to"][1]) * 0.00328084, 0)
            gr = DB.Grid.Create(doc, Line.CreateBound(p1, p2))
            try:
                gr.Name = g["name"]
            except Exception:
                out["err"].append([str(g.get("name")), "name-clash"])
            out["made"].append([to_text(gr.Name),
                                int(gr.Id.IntegerValue)])
        except Exception as ex:
            out["err"].append([str(g.get("name")), to_text(ex)[:80]])
    t.Commit()
except Exception as ex:
    try:
        t.RollBack()
    except Exception:
        pass
    out["err"].append(["txn", to_text(ex)[:120]])
_result = out
'''


def grids_create(specs):
    return GRIDS_CREATE.replace("__G__", J({"grids": specs}))


# -------------------------------------------------------------- stairs
STAIRS_U = u'''# -*- coding: utf-8 -*-
import Autodesk.Revit.DB as DB
import Autodesk.Revit.DB.Architecture as ARCH
__PRE__
import json
G = json.loads(r\'__G__\')
out = {}
ft = 0.00328084

def L(nm):
    for l in FilteredElementCollector(doc).OfClass(Level).ToElements():
        if to_text(l.Name) == nm:
            return l
    raise Exception("no level " + nm)

bl = L(G["from"])
tl = L(G["to"])
just = ARCH.StairsRunJustification.Center
scope = DB.StairsEditScope(doc, "gen stairs")
sid = scope.Start(bl.Id, tl.Id)
out["stairs"] = int(sid.IntegerValue)
t = Transaction(doc, "gen stairs txn")
pre = _prep(t)
t.Start()
try:
    r1 = G["run1"]
    r2 = G["run2"]
    run1 = ARCH.StairsRun.CreateStraightRun(
        doc, sid,
        Line.CreateBound(XYZ(r1[0] * ft, r1[1] * ft, bl.Elevation),
                         XYZ(r1[2] * ft, r1[3] * ft, bl.Elevation)), just)
    out["run1"] = int(run1.Id.IntegerValue)
    run2 = ARCH.StairsRun.CreateStraightRun(
        doc, sid,
        Line.CreateBound(XYZ(r2[0] * ft, r2[1] * ft, bl.Elevation),
                         XYZ(r2[2] * ft, r2[3] * ft, bl.Elevation)), just)
    out["run2"] = int(run2.Id.IntegerValue)
    t.Commit()
    out["txn_ok"] = True
    scope.Commit(pre)
    out["scope_ok"] = True
    st = doc.GetElement(sid)
    out["runs"] = len(list(st.GetStairsRuns()))
    t2 = Transaction(doc, "gen landing")
    pre2 = _prep(t2)
    t2.Start()
    fts = list(FilteredElementCollector(doc).OfClass(FloorType)
               .ToElements())
    arr = CurveArray()
    cs = [XYZ(p[0] * ft, p[1] * ft, bl.Elevation) for p in G["landing"]]
    for i in range(4):
        arr.Append(Line.CreateBound(cs[i], cs[(i + 1) % 4]))
    fl = doc.Create.NewFloor(arr, fts[0], bl, False)
    p = fl.get_Parameter(BuiltInParameter.FLOOR_HEIGHTABOVELEVEL_PARAM)
    if p is not None and not p.IsReadOnly:
        p.Set(G["landing_offset"] * ft)
        out["offset"] = G["landing_offset"]
    t2.Commit()
    out["landing"] = int(fl.Id.IntegerValue)
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


def stairs_u(geom):
    return STAIRS_U.replace("__G__", J(geom))


# ---------------------------------------------------------------- roof
GABLE_ROOF = u'''# -*- coding: utf-8 -*-
__PRE__
import json
import math
G = json.loads(r\'__G__\')
out = {"variants": []}
ft = 0.00328084
lvls = sorted(FilteredElementCollector(doc).OfClass(Level).ToElements(),
              key=lambda l: l.Elevation)
lvl = None
for l in lvls:
    if to_text(l.Name) == G["level"]:
        lvl = l
if lvl is None:
    lvl = lvls[-1]
rts = list(FilteredElementCollector(doc).OfClass(RoofType).ToElements())
views = list(FilteredElementCollector(doc).OfClass(ViewPlan).ToElements())
v = views[0]
ft = 0.00328084
z0 = lvl.Elevation
s0, s1 = G["span"]
c0, c1 = G["cross"]
sm = (s0 + s1) / 2.0
cm = (c0 + c1) / 2.0
rise = (c1 - c0) / 2.0 * math.tan(math.radians(G["slope"]))

def attempt(tag, rt):
    t = Transaction(doc, "roof " + tag)
    p = _prep(t)
    t.Start()
    try:
        if G["axis"] == "x":
            p0 = XYZ(sm * ft, c0 * ft, 0)
            p1 = XYZ(sm * ft, c1 * ft, 0)
            cut = XYZ(1, 0, 0)
            prof = [(sm, c0), (sm, cm), (sm, c1)]
        else:
            p0 = XYZ(c0 * ft, sm * ft, 0)
            p1 = XYZ(c1 * ft, sm * ft, 0)
            cut = XYZ(0, 1, 0)
            prof = [(c0, sm), (cm, sm), (c1, sm)]
        rp = doc.Create.NewReferencePlane2(p0, p1, cut, v)
        ca = CurveArray()
        pts = [(prof[0][0] * ft, prof[0][1] * ft, z0),
               (prof[1][0] * ft, prof[1][1] * ft, z0 + rise * ft),
               (prof[2][0] * ft, prof[2][1] * ft, z0)]
        for i in range(3):
            ca.Append(Line.CreateBound(XYZ(*pts[i]), XYZ(*pts[(i + 1) % 3])))
        roof = doc.Create.NewExtrusionRoof(
            ca, rp, lvl, rt, (s0 - sm) * ft, (s1 - sm) * ft)
        t.Commit()
        return {"tag": tag, "id": int(roof.Id.IntegerValue)}
    except Exception as ex:
        try:
            t.RollBack()
        except Exception:
            pass
        return {"tag": tag, "err": to_text(ex)[:90]}

for rt in rts:
    vr = attempt("ext_" + str(len(out["variants"])), rt)
    out["variants"].append(vr)
    if "id" in vr:
        out["made"] = vr
        break
if "made" not in out:
    t = Transaction(doc, "roof flat fallback")
    p = _prep(t)
    t.Start()
    try:
        fts = list(FilteredElementCollector(doc).OfClass(FloorType)
                   .ToElements())
        fx = G["flat"]
        pts = [(fx[0], fx[1]), (fx[2], fx[1]), (fx[2], fx[3]),
               (fx[0], fx[3])]
        arr = CurveArray()
        cs = [XYZ(x * ft, y * ft, z0) for x, y in pts]
        for i in range(4):
            arr.Append(Line.CreateBound(cs[i], cs[(i + 1) % 4]))
        fl = doc.Create.NewFloor(arr, fts[0], lvl, False)
        t.Commit()
        # 2026-10-02：把"其实是楼板"这件事**显式标出来**。
        #   此前这条兜底返回了一个正常 id，引擎就把它当屋面记进 pools["roofs"]，
        #   于是失败被伪装成了成功 —— 三层社区服务中心/四层别墅 的屋面
        #   都是这么变成楼板的，一直没人发现。
        #   引擎见到 degraded 必须告警，不能静默计入成功。
        out["made"] = {"tag": "flat_fallback",
                       "id": int(fl.Id.IntegerValue),
                       "degraded": "floor"}
    except Exception as ex:
        try:
            t.RollBack()
        except Exception:
            pass
        out["made"] = {"tag": "flat_fallback", "err": to_text(ex)[:90]}
_result = out
'''


def gable_roof(geom):
    return GABLE_ROOF.replace("__G__", J(geom))


# ------------------------------------------------------------- railing
BALCONY_RAILING = u'''# -*- coding: utf-8 -*-
import Autodesk.Revit.DB.Architecture as ARCH
__PRE__
import json
G = json.loads(r\'__G__\')
out = {}
ft = 0.00328084
lvl = None
for l in FilteredElementCollector(doc).OfClass(Level).ToElements():
    if to_text(l.Name) == G["level"]:
        lvl = l
if lvl is None:
    raise Exception("no level " + G["level"])
rts = list(FilteredElementCollector(doc).OfClass(ARCH.RailingType)
           .ToElements())
t = Transaction(doc, "gen railing")
pre = _prep(t)
t.Start()
made = None
errs = []
for rt in rts:
    try:
        corners = [XYZ(x * ft, y * ft, lvl.Elevation) for x, y in G["path"]]
        cl = CurveLoop.Create([Line.CreateBound(corners[i],
                                                corners[i + 1])
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


def balcony_railing(level, path):
    return BALCONY_RAILING.replace("__G__", J({"level": level,
                                               "path": path}))


# ------------------------------------------------------------ families
LOAD_FAMILIES = u'''# -*- coding: utf-8 -*-
__PRE__
import json
G = json.loads(r\'__G__\')
out = {}
t = Transaction(doc, "gen load families")
pre = _prep(t)
t.Start()
for p in G["paths"]:
    k = "\\\\".join(p.split("\\\\")[-2:])
    try:
        ok = doc.LoadFamily(p)
        out[k] = ("loaded" if ok else "already/failed")
    except Exception as ex:
        out[k] = to_text(ex)[:80]
t.Commit()
_result = out
'''


def load_families(paths):
    return LOAD_FAMILIES.replace("__G__", J({"paths": paths}))


# ----------------------------------------------------- doors / windows
DOORS_WINDOWS = u'''# -*- coding: utf-8 -*-
import Autodesk.Revit.DB as DB
from Autodesk.Revit.DB.Structure import StructuralType
__PRE__
import json
out = {"placed": 0, "fails": [], "audit": []}
ft = 0.00328084
G = json.loads(r\'__G__\')
ops = G["ops"]
lvls = sorted(FilteredElementCollector(doc).OfClass(Level).ToElements(),
              key=lambda l: l.Elevation)
cat = BuiltInCategory.OST_Doors if G["kind"] == "door" \\
    else BuiltInCategory.OST_Windows
syms = []
for e in FilteredElementCollector(doc).OfCategory(cat).ToElements():
    if isinstance(e, FamilySymbol):
        syms.append(e)
if len(syms) == 0:
    out["fails"].append("no symbols")
    _result = out
else:
    # 找墙缓存: Wall 收集器与基面参数一次读取 (原每 op 重扫全文档,
    # N 个开洞 = N 次全类别扫描)
    wall_cache = []
    for w0 in FilteredElementCollector(doc).OfClass(Wall).ToElements():
        p0 = w0.get_Parameter(BuiltInParameter.WALL_BASE_CONSTRAINT)
        if p0 is None or p0.AsElementId() is None:
            continue
        wall_cache.append((w0, int(p0.AsElementId().IntegerValue)))
    t = Transaction(doc, "gen " + G["kind"])
    pre = _prep(t)
    t.Start()
    # Activate 必须在事务内(事务外静默失败 -> "symbol is not active")
    for s in syms:
        try:
            s.Activate()
        except Exception:
            pass
    placed_ids = []
    for op in ops:
        try:
            px, py = op["at"][0] * ft, op["at"][1] * ft
            want_w = op.get("w", 900)
            want_h = op.get("h", 0) or (2100 if G["kind"] == "door"
                                        else 1500)
            sill = op.get("sill", 0) or 0
            lvl = None
            for l in lvls:
                if to_text(l.Name) == op["level"]:
                    lvl = l
            if lvl is None:
                out["fails"].append("op %s: no level" % str(op["at"]))
                continue
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
                out["fails"].append(
                    "op %s: no wall" % str(op["at"]))
                continue
            w, ppt = best
            pool = syms
            fh = op.get("fam")
            if fh:
                # 族名精确匹配优先(防"固定窗"误配"拱顶形固定窗"),
                # 无精确命中再退子串
                cands = [s for s in syms
                         if to_text(s.Family.Name) == fh]
                if not cands:
                    cands = []
                    for s in syms:
                        try:
                            if fh in to_text(s.Family.Name):
                                cands.append(s)
                        except Exception:
                            pass
                if cands:
                    pool = cands
            def _sym_wh(s):
                wv = hv = None
                try:
                    p = s.get_Parameter(
                        BuiltInParameter.FAMILY_WIDTH_PARAM)
                    if p is not None and p.HasValue:
                        wv = p.AsDouble() / ft
                except Exception:
                    pass
                try:
                    p = s.get_Parameter(
                        BuiltInParameter.FAMILY_HEIGHT_PARAM)
                    if p is not None and p.HasValue:
                        hv = p.AsDouble() / ft
                except Exception:
                    pass
                return wv, hv
            # 精确找型(宽高双配, ±1.5mm); 缺失则复制最近型改名设参
            sym = None
            for s in pool:
                wv, hv = _sym_wh(s)
                if (wv is not None and hv is not None and
                        abs(wv - want_w) < 1.5 and
                        abs(hv - want_h) < 1.5):
                    sym = s
                    break
            if sym is None:
                base = None
                bd = 1e18
                for s in pool:
                    wv, hv = _sym_wh(s)
                    if wv is None:
                        continue
                    d2 = (abs(wv - want_w) +
                          abs((hv or 0.0) - want_h) * 0.3)
                    if d2 < bd:
                        bd = d2
                        base = s
                if base is not None:
                    try:
                        nm = "MCP-%s-%dx%d" % (G["kind"][0],
                                               int(want_w), int(want_h))
                        try:
                            dup = base.Duplicate(nm)
                        except Exception:
                            dup = base.Duplicate(nm + "-r2")
                        # 回填池: 同尺寸后续开洞直接精确命中,
                        # 否则撞名回退基础型导致高度失配
                        syms.append(dup)
                        try:
                            dup.Name = nm
                        except Exception:
                            pass
                        pw = dup.get_Parameter(
                            BuiltInParameter.FAMILY_WIDTH_PARAM)
                        if pw is not None and not pw.IsReadOnly:
                            pw.Set(want_w * ft)
                        ph = dup.get_Parameter(
                            BuiltInParameter.FAMILY_HEIGHT_PARAM)
                        if ph is not None and not ph.IsReadOnly:
                            ph.Set(want_h * ft)
                        try:
                            dup.Activate()
                        except Exception:
                            pass
                        sym = dup
                    except Exception:
                        sym = base
            if sym is None:
                sym = pool[0] if pool else syms[0]
            z = lvl.Elevation + sill * ft
            ppt2 = XYZ(ppt.X, ppt.Y, z)
            inst = doc.Create.NewFamilyInstance(
                ppt2, sym, w, lvl, StructuralType.NonStructural)
            if inst is None:
                out["fails"].append("op %s: none" % str(op["at"]))
                continue
            for pn, val in ((u"\\u5bbd\\u5ea6", want_w * ft),
                            (u"\\u9ad8\\u5ea6", want_h * ft),
                            (u"\\u5e95\\u9ad8\\u5ea6", sill * ft)):
                try:
                    ip = inst.LookupParameter(pn)
                    if ip is not None and not ip.IsReadOnly:
                        ip.Set(val)
                except Exception:
                    pass
            placed_ids.append(int(inst.Id.IntegerValue))
            out["placed"] += 1
        except Exception as ex:
            out["fails"].append("op %s: %s" % (
                str(op["at"]), to_text(ex)[:100]))
    t.Commit()
    for iid in placed_ids:
        try:
            el = doc.GetElement(ElementId(iid))
            if el is None:
                out["audit"].append([iid, "DIED"])
                continue
            bb = el.get_BoundingBox(None)
            h = el.Host
            hb = h.get_BoundingBox(None) if h is not None else None
            if bb is None or hb is None:
                out["audit"].append([iid, "NOBB"])
                continue
            ok = (bb.Min.Z >= hb.Min.Z - ft and
                  bb.Max.Z <= hb.Max.Z + ft)
            out["audit"].append([iid, round(bb.Min.Z / ft),
                                 round(bb.Max.Z / ft),
                                 round(hb.Min.Z / ft),
                                 round(hb.Max.Z / ft),
                                 "OK" if ok else "OUT"])
        except Exception as ex:
            out["audit"].append([iid, "ERR", to_text(ex)[:60]])
    _result = out
'''


def doors_windows(kind, ops):
    return DOORS_WINDOWS.replace("__G__", J({"kind": kind, "ops": ops}))


# ------------------------------------------------------------- save-as
# 桥的 save_document 在文档已有 PathName 时永远只 doc.Save()(忽略
# path/mode, 7.10.4 冻结不改) -> takeover 另存走 execute_code。
# 实测坑: 文档自身路径==目标路径时 SaveAs 报 "File already exists!"
# (重跑场景) -> 路径一致走 doc.Save(), 不一致才 SaveAs。
SAVE_AS = u'''# -*- coding: utf-8 -*-
import json
G = json.loads(r\'__G__\')
out = {"saved": False}
try:
    cur = to_text(doc.PathName or "").lower().replace(chr(92), "/")
    want = G["path"].lower().replace(chr(92), "/")
    if cur == want:
        doc.Save()
        out["mode"] = "save"
    else:
        doc.SaveAs(G["path"])
        out["mode"] = "saveas"
    out["saved"] = True
    out["path"] = to_text(doc.PathName)
    out["title"] = to_text(doc.Title)
except Exception as ex:
    out["err"] = to_text(ex)[:200]
_result = out
'''


def save_as(path):
    return SAVE_AS.replace("__G__", J({"path": path}))


# ---------------------------------------------------------- supersede
# dwg 骨架墙被精修墙几何覆盖时删除(原 rebuild_05 用 delete_ids 硬编码,
# 但骨架墙 ElementId 每次重建都漂移 -> 改为按中心线覆盖率判定):
# 对每面骨架墙, 把各精修墙段的端点投影到骨架墙参数轴上形成区间,
# 区间并集总长 / 墙长 >= min_cov 且端点垂距 <= tol -> 删。
DELETE_SUPERSEDED = u'''# -*- coding: utf-8 -*-
__PRE__
import json
G = json.loads(r\'__G__\')
out = {"deleted": [], "kept": 0}
ft = 0.00328084
tol2 = float(G.get("tol", 15)) ** 2
min_cov = float(G.get("min_cov", 0.95))
segs = [[float(v) for v in s] for s in G["segs"]]
keep = set(int(i) for i in G.get("keep_ids") or [])
lvls = sorted(FilteredElementCollector(doc).OfClass(Level).ToElements(),
              key=lambda l: l.Elevation)
lvl = None
for l in lvls:
    if to_text(l.Name) == G["level"]:
        lvl = l

def proj(px, py, x1, y1, x2, y2):
    vx, vy = x2 - x1, y2 - y1
    L2 = vx * vx + vy * vy
    if L2 == 0:
        return None
    tt = ((px - x1) * vx + (py - y1) * vy) / L2
    cx, cy = x1 + tt * vx, y1 + tt * vy
    d2 = (cx - px) ** 2 + (cy - py) ** 2
    return (d2, tt)

t = Transaction(doc, "gen supersede")
pre = _prep(t)
t.Start()
for w in FilteredElementCollector(doc).OfClass(Wall).ToElements():
    try:
        if w.Id.IntegerValue in keep:
            continue
        if lvl is not None:
            p = w.get_Parameter(BuiltInParameter.WALL_BASE_CONSTRAINT)
            if p is None or p.AsElementId() is None:
                continue
            if p.AsElementId().IntegerValue != lvl.Id.IntegerValue:
                continue
        c = w.Location.Curve
        a = c.GetEndPoint(0)
        b = c.GetEndPoint(1)
        ax, ay, bx, by = a.X / ft, a.Y / ft, b.X / ft, b.Y / ft
        vx, vy = bx - ax, by - ay
        Lw = (vx * vx + vy * vy) ** 0.5
        if Lw < 1.0:
            continue
        ivs = []
        for s in segs:
            r1 = proj(s[0], s[1], ax, ay, bx, by)
            r2 = proj(s[2], s[3], ax, ay, bx, by)
            if r1 is None or r2 is None:
                continue
            if r1[0] > tol2 or r2[0] > tol2:
                continue
            lo, hi = sorted((r1[1], r2[1]))
            if hi > 0.0 and lo < 1.0:
                ivs.append((max(0.0, lo), min(1.0, hi)))
        ivs.sort()
        cov = 0.0
        cur_lo = cur_hi = None
        for lo, hi in ivs:
            if cur_hi is None or lo > cur_hi:
                if cur_hi is not None:
                    cov += cur_hi - cur_lo
                cur_lo, cur_hi = lo, hi
            elif hi > cur_hi:
                cur_hi = hi
        if cur_hi is not None:
            cov += cur_hi - cur_lo
        if cov / Lw >= min_cov:
            doc.Delete(w.Id)
            out["deleted"].append(int(w.Id.IntegerValue))
        else:
            out["kept"] += 1
    except Exception:
        out["kept"] += 1
t.Commit()
out["n_deleted"] = len(out["deleted"])
_result = out
'''


def supersede(level, segs, keep_ids, tol=15, min_cov=0.95):
    return DELETE_SUPERSEDED.replace("__G__", J(
        {"level": level, "segs": segs, "keep_ids": keep_ids,
         "tol": tol, "min_cov": min_cov}))


# --------------------------------------------------- gable free-form
# NewExtrusionRoof 全变体 Invalid profile / NewFootPrintRoof 全签名 null
# (17 次实证, 桥 7.10.4 含 create_roof_gable 也 null) -> 坡屋顶改用
# GeometryCreationUtilities 挤出三角棱柱 + DirectShape.Create(SetShape),
# 一次成体: 双坡面 + 双山墙端, span 含挑檐。
# FreeFormElement.Create 只支持族文档(项目文档实测报
# "document is not a family document"), 项目文档走 DirectShape。
GABLE_FREEFORM = u'''# -*- coding: utf-8 -*-
from Autodesk.Revit.DB import (GeometryCreationUtilities,
                               DirectShape, DirectShapeType, GeometryObject)
from System.Collections.Generic import List
__PRE__
import json
import math
G = json.loads(r\'__G__\')
out = {}
ft = 0.00328084
lvl = None
for l in FilteredElementCollector(doc).OfClass(Level).ToElements():
    if to_text(l.Name) == G["level"]:
        lvl = l
if lvl is None:
    raise Exception("no level " + G["level"])
z0 = lvl.Elevation
s0, s1 = G["span"]
c0, c1 = G["cross"]
cm = (c0 + c1) / 2.0
rise = (c1 - c0) / 2.0 * math.tan(math.radians(G.get("slope", 30)))
if G.get("axis", "x") != "x":
    a = XYZ(c0 * ft, s0 * ft, z0)
    b = XYZ(cm * ft, s0 * ft, z0 + rise * ft)
    c = XYZ(c1 * ft, s0 * ft, z0)
    direction = XYZ(0, 1, 0)
else:
    a = XYZ(s0 * ft, c0 * ft, z0)
    b = XYZ(s0 * ft, cm * ft, z0 + rise * ft)
    c = XYZ(s0 * ft, c1 * ft, z0)
    direction = XYZ(1, 0, 0)
loop = CurveLoop.Create([Line.CreateBound(a, b), Line.CreateBound(b, c),
                         Line.CreateBound(c, a)])
solid = GeometryCreationUtilities.CreateExtrusionGeometry(
    [loop], direction, (s1 - s0) * ft)
t = Transaction(doc, "gen gable freeform")
pre = _prep(t)
t.Start()
try:
    objs = List[GeometryObject]()
    objs.Add(solid)
    # 2026-10-02 修：类别从 OST_GenericModel 改成 OST_Roofs。
    #   实测（活体 Revit 2019）：DirectShape 允许建到屋顶类别，
    #       DirectShape.CreateElement(doc, ElementId(OST_Roofs))
    #       -> {ok, category:"屋顶", class:"DirectShape"}
    #   真屋面 API（NewExtrusionRoof / NewFootPrintRoof）在 2019 上确实
    #   建不出来（Revit 内部抛 "Value cannot be null"），所以这里仍是
    #   **体量替身，不是参数化 Roof** —— 不能编辑坡度、不参与屋顶连接。
    #   但归到屋顶类别后，屋面视图/过滤器/明细表都能找到它，
    #   比丢进"常规模型"正确得多。
    # 2019: CreateElement / 新版: Create
    if hasattr(DirectShape, "CreateElement"):
        ds = DirectShape.CreateElement(
            doc, ElementId(BuiltInCategory.OST_Roofs))
    else:
        ds = DirectShape.Create(
            doc, ElementId(BuiltInCategory.OST_Roofs))
    ds.SetShape(objs)
    # 2026-10-02：给屋面体量一个**有名字的屋顶类别类型**。
    #   实测：DirectShapeType.Create(doc, name, ElementId(OST_Roofs)) 可用，
    #   且 ds.SetTypeId(dst.Id) 挂得上（element 仍是 DirectShape，类别=屋顶）。
    #   为什么值得做：没有类型的 DirectShape 在屋面明细表里只能归到"默认"，
    #   有了类型名就能按"BIMT-双坡屋面-25度"分组统计、也能在属性栏看到。
    #   这是在"参数化屋面(NewFootPrintRoof/NewExtrusionRoof)在本机 Revit 2019
    #   上实测 44+ 种组合全部失败"的前提下，能做到的最接近真屋面的形态。
    try:
        tname = u"BIMT-\\u53cc\\u5761\\u5c4b\\u9762-%d\\u5ea6" % int(G.get("slope", 30))
    except Exception:
        tname = u"BIMT-\\u5c4b\\u9762"
    try:
        dst = None
        for _t in FilteredElementCollector(doc).OfClass(DirectShapeType) \\
                .ToElements():
            # 两种取名方式都试：IronPython 下 get_Name() 在部分类型上不可用，
            # 只试一种会导致"找不到已有类型 -> 每次构建都新建一个"。
            _n = None
            for _a in ("Name", "get_Name"):
                try:
                    _v = getattr(_t, _a)
                    _n = to_text(_v() if callable(_v) else _v)
                    break
                except Exception:
                    continue
            if _n == tname:
                dst = _t
                break
        if dst is None:
            dst = DirectShapeType.Create(
                doc, tname, ElementId(BuiltInCategory.OST_Roofs))
        ds.SetTypeId(dst.Id)
        out["type_name"] = tname
        out["type_id"] = int(dst.Id.IntegerValue)
    except Exception as ex:
        out["type_err"] = to_text(ex)[:160]
    t.Commit()
    out["id"] = int(ds.Id.IntegerValue)
    out["rise_mm"] = round(rise)
except Exception as ex:
    out["err"] = to_text(ex)[:200]
    try:
        t.RollBack()
    except Exception:
        pass
_result = out
'''


def gable_freeform(geom):
    return GABLE_FREEFORM.replace("__G__", J(geom))


# ---------------------------------------------------------- finishes
ENSURE_MATERIAL = u'''# -*- coding: utf-8 -*-
from Autodesk.Revit.DB import Material, Color
__PRE__
import json
G = json.loads(r\'__G__\')
out = {}
want = G.get("name", "")
mat = None
for e in FilteredElementCollector(doc).WhereElementIsNotElementType() \\
        .ToElements():
    try:
        if to_text(type(e).__name__) == "Material":
            if to_text(e.Name) == want:
                mat = e
                break
    except Exception:
        pass
if mat is None:
    t0 = Transaction(doc, "gen ensure material")
    p0 = _prep(t0)
    t0.Start()
    try:
        mid = Material.Create(doc, want)
        mat = doc.GetElement(mid)
        try:
            mat.Color = Color(int(G.get("r", 255)), int(G.get("g", 255)),
                              int(G.get("b", 255)))
            mat.Transparency = int(G.get("transparency", 0))
            mat.Smoothness = int(G.get("smoothness", 60))
        except Exception:
            pass
        t0.Commit()
        out["created"] = True
    except Exception as ex:
        try:
            t0.RollBack()
        except Exception:
            pass
        out["err"] = to_text(ex)[:120]
else:
    out["found"] = True
if mat is not None:
    out["id"] = int(mat.Id.IntegerValue)
    out["name"] = to_text(mat.Name)
_result = out
'''


def ensure_material(mat):
    return ENSURE_MATERIAL.replace("__G__", J(mat or {}))


FINISH_TYPE = u'''# -*- coding: utf-8 -*-
from Autodesk.Revit.DB import CompoundStructureLayer
from System.Collections.Generic import List
__PRE__
import json
G = json.loads(r\'__G__\')
out = {}
kind = G.get("kind", "wall")
cls = {"wall": WallType, "floor": FloorType, "roof": RoofType}.get(
    kind, WallType)
def _type_name(e):
    # SystemFamilyType(.Name) 在 exec 沙箱里抛 AttributeError ->
    # 优先走 SYMBOL_NAME_PARAM 读类型名; 全部失败返回空串(不炸宏)
    try:
        p = e.get_Parameter(BuiltInParameter.SYMBOL_NAME_PARAM)
        if p is not None:
            v = p.AsString()
            if v:
                return to_text(v)
    except Exception:
        pass
    try:
        return to_text(e.Name)
    except Exception:
        return ""

tgt = None
if G.get("wall_id"):
    # 快路径: 由墙实例反查其类型(墙由桥按厚度建 MCP-W-<t>mm,
    # 同厚共享一型), 完全绕开类型名读取
    w = doc.GetElement(ElementId(int(G["wall_id"])))
    if w is not None:
        tgt = w.WallType
if tgt is None:
    for wt in FilteredElementCollector(doc).OfClass(cls).ToElements():
        if _type_name(wt) == G["type_name"]:
            tgt = wt
            break
if tgt is None:
    out["err"] = "no type " + G["type_name"]
else:
    t = Transaction(doc, "gen finish " + G["type_name"])
    pre = _prep(t)
    t.Start()
    try:
        cs = tgt.GetCompoundStructure()
        layers = list(cs.GetLayers())
        best = 0
        bw = -1.0
        for i in range(len(layers)):
            w = abs(layers[i].Width)
            if w > bw:
                bw = w
                best = i
        try:
            # 新版 API
            cs.SetLayerMaterial(best, ElementId(int(G["mid"])))
        except Exception:
            # 2019 API: 取层对象改 MaterialId 后整体回写
            layers[best].MaterialId = ElementId(int(G["mid"]))
            lst = List[CompoundStructureLayer]()
            for ly in layers:
                lst.Add(ly)
            cs.SetLayers(lst)
        tgt.SetCompoundStructure(cs)
        t.Commit()
        out["layer"] = best
        out["ok"] = True
    except Exception as ex:
        try:
            t.RollBack()
        except Exception:
            pass
        out["err"] = to_text(ex)[:150]
_result = out
'''


def finish_type(kind, type_name, mid, wall_id=None):
    return FINISH_TYPE.replace("__G__", J(
        {"kind": kind, "type_name": type_name, "mid": int(mid),
         "wall_id": int(wall_id) if wall_id else None}))


FINISH_ELEMENTS = u'''# -*- coding: utf-8 -*-
__PRE__
import json
G = json.loads(r\'__G__\')
out = {"assigned": 0, "skip": 0}
for iid in G["ids"]:
    try:
        e = doc.GetElement(ElementId(int(iid)))
        if e is None:
            out["skip"] += 1
            continue
        p = e.get_Parameter(BuiltInParameter.ROOT_MATERIAL_PARAM)
        if p is None or p.IsReadOnly:
            p = e.get_Parameter(BuiltInParameter.MATERIAL_ID_PARAM)
        if p is not None and not p.IsReadOnly:
            p.Set(ElementId(int(G["mid"])))
            out["assigned"] += 1
        else:
            out["skip"] += 1
    except Exception:
        out["skip"] += 1
_result = out
'''


def finish_elements(ids, mid):
    return FINISH_ELEMENTS.replace("__G__", J(
        {"ids": [int(i) for i in ids], "mid": int(mid)}))


# 墙型定型: 桥 _wall_type_for 的 wt.Name 在本环境对 WallType 抛
# AttributeError(其 except: pass 静默跳过全部类型) -> 兜底
# base.Duplicate(name) 撞重名再 except: return base -> 静默返回
# 错误厚度基础型(实测 17 墙全部 138mm 隔断)。桥冻结不改 ->
# 引擎侧按池修复: SYMBOL_NAME_PARAM(实证可用) 找/建 MCP-W-<t>mm
# + 单层精确厚度复合结构 + ChangeTypeId 批量改型。
SET_WALL_TYPES = u'''# -*- coding: utf-8 -*-
from Autodesk.Revit.DB import (CompoundStructureLayer,
                               MaterialFunctionAssignment, Material)
from System.Collections.Generic import List
__PRE__
import json
G = json.loads(r\'__G__\')
out = {"groups": []}

def _tname(e):
    try:
        p = e.get_Parameter(BuiltInParameter.SYMBOL_NAME_PARAM)
        if p is not None:
            v = p.AsString()
            if v:
                return to_text(v)
    except Exception:
        pass
    return ""

t = Transaction(doc, "gen wall types")
pre = _prep(t)
t.Start()
for grp in G["groups"]:
    nm = "MCP-W-%dmm" % int(grp["t"])
    tgt = None
    for wt in FilteredElementCollector(doc).OfClass(WallType).ToElements():
        if _tname(wt) == nm:
            tgt = wt
            break
    made = "found"
    if tgt is None:
        base = None
        try:
            base = doc.GetElement(
                ElementId(int(grp["ids"][0]))).WallType
        except Exception:
            base = None
        if base is None:
            out["groups"].append({"t": grp["t"], "err": "no base"})
            continue
        try:
            tgt = base.Duplicate(nm)
            made = "dup"
        except Exception:
            tgt = None
            for wt in FilteredElementCollector(doc).OfClass(
                    WallType).ToElements():
                if _tname(wt) == nm:
                    tgt = wt
                    break
            if tgt is None:
                out["groups"].append(
                    {"t": grp["t"], "err": "dup conflict"})
                continue
    try:
        cs = tgt.GetCompoundStructure()
        midv = -1
        try:
            for ly0 in cs.GetLayers():
                mv = ly0.MaterialId.IntegerValue
                if mv > 0:
                    midv = mv
                    break
        except Exception:
            pass
        if midv <= 0:
            for m0 in FilteredElementCollector(doc).OfClass(
                    Material).ToElements():
                midv = int(m0.Id.IntegerValue)
                break
        ly = CompoundStructureLayer(
            int(grp["t"]) * 0.00328084,
            MaterialFunctionAssignment.Structure,
            ElementId(midv))
        lst = List[CompoundStructureLayer]()
        lst.Add(ly)
        cs.SetLayers(lst)
        tgt.SetCompoundStructure(cs)
    except Exception as ex:
        out["groups"].append({"t": grp["t"],
                              "err": to_text(ex)[:80],
                              "at": "compound"})
        continue
    n = 0
    for wid in grp["ids"]:
        try:
            w2 = doc.GetElement(ElementId(int(wid)))
            if w2 is not None:
                w2.ChangeTypeId(tgt.Id)
                n += 1
        except Exception:
            pass
    out["groups"].append({"t": grp["t"],
                          "type": int(tgt.Id.IntegerValue),
                          "how": made, "retyped": n})
t.Commit()
_result = out
'''


def set_wall_types(groups):
    return SET_WALL_TYPES.replace("__G__", J(
        {"groups": [{"t": int(g["t"]), "ids": [int(i) for i in g["ids"]]}
                    for g in groups]}))


# 门亭柱: 建筑柱(非结构) 4参 NewFamilyInstance(点, 符号, 底标高,
# NON_STRUCTURAL) + FAMILY_TOP_LEVEL_PARAM 拉到上一层; 尺寸参数按
# 别名表回写(矩形柱族 b/h 或 宽/深); 材质走 finishes
# target:columns (实例 MATERIAL_ID_PARAM)。
PLACE_COLUMNS = u'''# -*- coding: utf-8 -*-
from Autodesk.Revit.DB.Structure import StructuralType
__PRE__
import json
G = json.loads(r\'__G__\')
out = {"placed": 0, "audit": []}
fam_kw = G.get("fam", "")
lvl = None
lvl_top = None
for l in FilteredElementCollector(doc).OfClass(Level).ToElements():
    if to_text(l.Name) == G["level"]:
        lvl = l
    if G.get("top") and to_text(l.Name) == G["top"]:
        lvl_top = l
if lvl is None:
    raise Exception("no level " + G["level"])
sym = None
for s in FilteredElementCollector(doc).OfClass(FamilySymbol).ToElements():
    try:
        if fam_kw in to_text(s.Family.Name):
            sym = s
            break
    except Exception:
        pass
if sym is None:
    out["err"] = "no family " + fam_kw
else:
    _ALIAS = {}
    for k, v in (G.get("set") or {}).items():
        _ALIAS[k] = [k, k.lower(), u"宽", u"深", u"高", u"直径",
                     "width", "depth", "height", "dia"]
    t = Transaction(doc, "gen columns")
    pre = _prep(t)
    t.Start()
    try:
        if not sym.IsActive:
            sym.Activate()
        for k, v in (G.get("set") or {}).items():
            for p in sym.Parameters:
                try:
                    dn = to_text(p.Definition.Name)
                    if dn in _ALIAS[k] and not p.IsReadOnly:
                        p.Set(float(v) * 0.00328084)
                except Exception:
                    pass
        ids = []
        for pt in G["pts"]:
            ppt = XYZ(float(pt[0]) * 0.00328084,
                      float(pt[1]) * 0.00328084, lvl.Elevation)
            inst = doc.Create.NewFamilyInstance(
                ppt, sym, lvl, StructuralType.NonStructural)
            if lvl_top is not None:
                ptop = inst.get_Parameter(
                    BuiltInParameter.FAMILY_TOP_LEVEL_PARAM)
                if ptop is not None and not ptop.IsReadOnly:
                    ptop.Set(lvl_top.Id)
            ids.append(int(inst.Id.IntegerValue))
        t.Commit()
        out["placed"] = len(ids)
        out["ids"] = ids
        for iid in ids:
            try:
                e = doc.GetElement(ElementId(iid))
                bb = e.get_BoundingBox(None)
                out["audit"].append([iid,
                    round(bb.Min.Z / 0.00328084),
                    round(bb.Max.Z / 0.00328084)])
            except Exception:
                pass
    except Exception as ex:
        try:
            t.RollBack()
        except Exception:
            pass
        out["err"] = to_text(ex)[:150]
_result = out
'''


def place_columns(spec):
    return PLACE_COLUMNS.replace("__G__", J(spec))


# 柱/门窗族实例的类型级材质: 实例常无 MATERIAL_ID_PARAM(柱实测无)
# -> 反查 Symbol 写其"材质"(ParameterType.Material) 参数。
SET_SYM_MATERIAL = u'''# -*- coding: utf-8 -*-
__PRE__
import json
G = json.loads(r\'__G__\')
out = {"assigned": 0, "skip": 0}
t = Transaction(doc, "gen sym material")
pre = _prep(t)
t.Start()
for iid in G["ids"]:
    try:
        e = doc.GetElement(ElementId(int(iid)))
        if e is None:
            out["skip"] += 1
            continue
        sym = e.Symbol
        done = False
        for p in sym.Parameters:
            try:
                if p.Definition.ParameterType == \
                        ParameterType.Material and not p.IsReadOnly:
                    p.Set(ElementId(int(G["mid"])))
                    done = True
                    break
            except Exception:
                pass
        if done:
            out["assigned"] += 1
        else:
            out["skip"] += 1
    except Exception:
        out["skip"] += 1
t.Commit()
_result = out
'''


def set_sym_material(ids, mid):
    return SET_SYM_MATERIAL.replace("__G__", J(
        {"ids": [int(i) for i in ids], "mid": int(mid)}))


VERIFY = u'''# -*- coding: utf-8 -*-
out = {}
for cname in ["OST_Walls", "OST_Doors", "OST_Windows", "OST_Floors",
              "OST_Roofs", "OST_Stairs", "OST_StairsRailing",
              "OST_Columns", "OST_StructuralColumns",
              "OST_GenericModel"]:
    try:
        bic = getattr(BuiltInCategory, cname)
        n = 0
        for e in FilteredElementCollector(doc).OfCategory(bic) \\
                .ToElements():
            if not isinstance(e, ElementType):
                n += 1
        out[cname] = n
    except Exception:
        out[cname] = "ERR"
_result = out
'''
