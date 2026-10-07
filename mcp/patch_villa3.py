# -*- coding: utf-8 -*-
u"""Villa patch v3: (A) wall curve z forensic; (B) LoadFamily in txn;
(C) roof 4-variant matrix + flat-slab fallback; (D) wipe + re-place all
doors/windows with ppt.z forced to level elevation + logging suppressor;
save + verify."""
import json
import sys

sys.path.insert(0, r"E:\bim-toolkit\mcp")
import build_villa2 as B  # noqa: E402

# --- nearest-width matcher on B.DOORS ---
OLD_MATCH = '''            sym = None
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
                sym = syms[0]'''
NEW_MATCH = '''            sym = None
            best_s = None
            best_d2 = 1e18
            for s in syms:
                try:
                    wp = s.get_Parameter(
                        BuiltInParameter.FAMILY_WIDTH_PARAM)
                    if wp is not None and wp.HasValue:
                        d2 = abs(wp.AsDouble() / ft - want_w)
                        if d2 < best_d2:
                            best_d2 = d2
                            best_s = s
                except Exception:
                    pass
            sym = best_s if best_s is not None else syms[0]'''
assert OLD_MATCH in B.DOORS, "matcher block not found"
DOORS_N = B.DOORS.replace(OLD_MATCH, NEW_MATCH)
# force placement z to level elevation (wall location curves sit at z=0)
OLD_PPT = '''            w, ppt = best'''
NEW_PPT = '''            w, ppt = best
            ppt = XYZ(ppt.X, ppt.Y, lvl.Elevation)'''
assert OLD_PPT + "\n" in DOORS_N, "ppt block not found"
DOORS_N = DOORS_N.replace(OLD_PPT, NEW_PPT)

# logging suppressor (batch-fast + failure texts)
PRE_LOG = u'''from Autodesk.Revit.DB import (IFailuresPreprocessor,
                               FailureProcessingResult, FailureSeverity,
                               FailureMessageAccessor)
from System.Collections.Generic import List as _DL
_SEEN = []

class _Pre(IFailuresPreprocessor):
    def PreprocessFailures(self, fa):
        try:
            ms = list(fa.GetFailureMessages())
        except Exception:
            ms = []
        for m in ms:
            try:
                _SEEN.append(to_text(m.GetSeverity()) + " | " +
                             to_text(m.GetDescriptionText())[:110])
            except Exception:
                pass
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
DOORS_L = DOORS_N.replace("__PRE__", PRE_LOG).replace(
    '''    t.Commit()
    out["ids"] = placed_ids[:8]''',
    '''    t.Commit()
    out["fails_seen"] = list(_SEEN)[:12]
    out["ids"] = placed_ids[:8]''')

# --- A. wall curve z forensic (read-only) ---
WALLPROBE = r'''# -*- coding: utf-8 -*-
out = {}
ft = 0.00328084
wid = 334570
w = doc.GetElement(ElementId(wid))
c = w.Location.Curve
out["curve_z"] = [round(c.GetEndPoint(0).Z / ft, 1),
                  round(c.GetEndPoint(1).Z / ft, 1)]
out["curve_xy"] = [[round(c.GetEndPoint(0).X / ft), round(c.GetEndPoint(0).Y / ft)],
                   [round(c.GetEndPoint(1).X / ft), round(c.GetEndPoint(1).Y / ft)]]
bb = w.get_BoundingBox(None)
out["bbox_z"] = [round(bb.Min.Z / ft, 1), round(bb.Max.Z / ft, 1)]
p = w.get_Parameter(BuiltInParameter.WALL_BASE_CONSTRAINT)
if p is not None:
    lid = p.AsElementId().IntegerValue
    lv = doc.GetElement(ElementId(lid))
    out["base_lvl"] = to_text(lv.Name) if lv is not None else "?"
prj = c.Project(XYZ(2400 * ft, 4800 * ft, 3000 * ft))
out["proj_z"] = round(prj.XYZPoint.Z / ft, 1) if prj is not None else None
_result = out
'''
B.run(WALLPROBE, u"A-墙线z取证")

# --- B. LoadFamily inside a transaction ---
LOADFAM = u'''# -*- coding: utf-8 -*-
__PRE__
out = {}
paths = [u"C:\\\\ProgramData\\\\Autodesk\\\\RVT 2019\\\\Libraries\\\\China\\\\建筑\\\\门\\\\普通门\\\\平开门\\\\单扇\\\\单嵌板木门 1.rfa",
         u"C:\\\\ProgramData\\\\Autodesk\\\\RVT 2019\\\\Libraries\\\\China\\\\建筑\\\\门\\\\普通门\\\\平开门\\\\双扇\\\\双面嵌板木门 1.rfa",
         u"C:\\\\ProgramData\\\\Autodesk\\\\RVT 2019\\\\Libraries\\\\China\\\\建筑\\\\窗\\\\普通窗\\\\固定窗\\\\固定窗.rfa"]
import os
t = Transaction(doc, "load families")
pre = _prep(t)
t.Start()
for p in paths:
    k = u"\\\\".join(p.split(u"\\\\")[-2:])
    try:
        ok = doc.LoadFamily(p)
        out[k] = ("loaded" if ok else "already/failed")
    except Exception as ex:
        out[k] = to_text(ex)[:80]
t.Commit()
_result = out
'''
B.run(LOADFAM, u"B-加载族(事务内)")

# --- C. roof variants + fallback ---
ROOF3 = r'''# -*- coding: utf-8 -*-
__PRE__
out = {"variants": []}
lvls = sorted(FilteredElementCollector(doc).OfClass(Level).ToElements(),
              key=lambda l: l.Elevation)
lvl = lvls[-1]
rts = list(FilteredElementCollector(doc).OfClass(RoofType).ToElements())
views = list(FilteredElementCollector(doc).OfClass(ViewPlan).ToElements())
v = views[0]
ft = 0.00328084
z0 = lvl.Elevation

def tri(profx, rise_mm):
    ca = CurveArray()
    a = XYZ(profx * ft, -600 * ft, z0)
    b = XYZ(profx * ft, 4500 * ft, z0 + rise_mm * ft)
    c = XYZ(profx * ft, 9600 * ft, z0)
    ca.Append(Line.CreateBound(a, b))
    ca.Append(Line.CreateBound(b, c))
    ca.Append(Line.CreateBound(c, a))
    return ca

def attempt(tag, cutx, profx, rise_mm, rt):
    t = Transaction(doc, "roof " + tag)
    p = _prep(t)
    t.Start()
    try:
        rp = doc.Create.NewReferencePlane2(
            XYZ(profx * ft, -600 * ft, 0),
            XYZ(profx * ft, 9600 * ft, 0), cutx, v)
        roof = doc.Create.NewExtrusionRoof(
            tri(profx, rise_mm), rp, lvl, rt, -6600.0 * ft, 6600.0 * ft)
        if profx != 6000:
            doc.Move(roof.Id, XYZ((6000 - profx) * ft, 0, 0))
        t.Commit()
        return {"tag": tag, "rt": to_text(rt.Name)[:20] if False else "x",
                "id": int(roof.Id.IntegerValue)}
    except Exception as ex:
        try:
            t.RollBack()
        except Exception:
            pass
        return {"tag": tag, "err": to_text(ex)[:90]}

out["variants"].append(attempt("cutX_x0", XYZ(1, 0, 0), 0, 1700, rts[0]))
out["variants"].append(attempt("cutZ_lift", XYZ(0, 0, 1), 0, 1700, rts[0]))
ok = None
for vr in out["variants"]:
    if "id" in vr:
        ok = vr
        break
if ok is None:
    for rt in rts[1:]:
        vr = attempt("cutX_" + str(len(out["variants"])),
                     XYZ(1, 0, 0), 0, 1700, rt)
        out["variants"].append(vr)
        if "id" in vr:
            ok = vr
            break
if ok is None:
    t = Transaction(doc, "roof flat fallback")
    p = _prep(t)
    t.Start()
    try:
        fts = list(FilteredElementCollector(doc).OfClass(FloorType)
                   .ToElements())
        pts = [(0, 0), (12000, 0), (12000, 9000), (0, 9000)]
        arr = CurveArray()
        cs = [XYZ(x * ft, y * ft, z0) for x, y in pts]
        for i in range(4):
            arr.Append(Line.CreateBound(cs[i], cs[(i + 1) % 4]))
        fl = doc.Create.NewFloor(arr, fts[0], lvl, False)
        t.Commit()
        ok = {"tag": "flat_fallback", "id": int(fl.Id.IntegerValue)}
        out["variants"].append(ok)
    except Exception as ex:
        try:
            t.RollBack()
        except Exception:
            pass
        out["variants"].append({"tag": "flat_fallback",
                                "err": to_text(ex)[:90]})
out["made"] = ok
_result = out
'''
res_roof = B.run(ROOF3, u"C-屋面矩阵")
roof_id = (res_roof.get("made") or {}).get("id")

# --- D. wipe + re-place doors/windows ---
WIPE_DW = u'''# -*- coding: utf-8 -*-
__PRE__
ids = []
for cat in (BuiltInCategory.OST_Doors, BuiltInCategory.OST_Windows):
    for e in FilteredElementCollector(doc).OfCategory(cat).ToElements():
        if isinstance(e, ElementType):
            continue
        ids.append(e.Id)
t = Transaction(doc, "wipe doors windows")
pre = _prep(t)
t.Start()
n = 0
for i in ids:
    try:
        doc.Delete(i)
        n += 1
    except Exception:
        pass
t.Commit()
_result = {"found": len(ids), "deleted": n}
'''
B.run(WIPE_DW, u"D-清空门窗")

li_map = {B.LVL[0]: 0, B.LVL[1]: 1, B.LVL[2]: 2, B.LVL[3]: 3}
for kind in ("door", "window"):
    ops = [o for o in B.openings() if o["kind"] == kind]
    for o in ops:
        o["li"] = li_map[o["level"]]
    code = DOORS_L.replace("__OPS__", json.dumps(ops)).replace(
        "__KIND__", kind)
    B.run(code, u"D-门窗重放-" + kind)

# --- save + verify ---
res = B.bridge("save_document", {"path": B.SAVE_PATH}, u"保存")
print(u"[保存]", json.dumps(res, ensure_ascii=False)[:200])
B.run(B.VERIFY, u"验收")
